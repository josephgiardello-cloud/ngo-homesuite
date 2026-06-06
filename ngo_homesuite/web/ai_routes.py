from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify, request, session, stream_with_context
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, select

from ngo_homesuite.ai.apex_client import OllamaClient, OllamaClientError
from ngo_homesuite.ai.minion_service import HomeSuiteMinion
from ngo_homesuite.ai.pii_redact import redact_pii
from ngo_homesuite.db.audit_log import log_event
from ngo_homesuite.models.core import db, AIConversation, AIMessage
from ngo_homesuite.prompts import NGO_APEX_POLICY_SYSTEM_PROMPT
from ngo_homesuite.web.rbac import roles_required


ai_bp = Blueprint("ai", __name__, url_prefix="/ai")

_USED_APPROVAL_TOKENS: dict[str, float] = {}
_APPROVAL_TOKEN_TTL_SEC = 300
_MINION_RATE_BUCKETS: dict[str, list[float]] = {}
_SESSION_KEY = "ngo_ai_session_id"


def _parse_tool_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    return []


def _parse_approved_actions(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []

    items = raw if isinstance(raw, list) else [raw]
    parsed: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            tool = str(item.get("tool", "")).strip()
            token = str(item.get("token", "")).strip()
            if tool:
                parsed.append({"tool": tool, "token": token})
        elif isinstance(item, str):
            tool = item.strip()
            if tool:
                parsed.append({"tool": tool, "token": ""})
    return parsed


def _approval_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=str(current_app.secret_key), salt="ngohs-minion-approval-v1")


def _approval_fingerprint(prompt: str, tool: str, user_id: Any, organization_id: Any) -> str:
    payload = {
        "prompt_sha256": hashlib.sha256(str(prompt).encode("utf-8")).hexdigest(),
        "tool": str(tool),
        "user_id": str(user_id),
        "organization_id": str(organization_id),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prune_used_approval_tokens(now_ts: float, ttl_sec: int) -> None:
    stale = [tok for tok, ts in _USED_APPROVAL_TOKENS.items() if now_ts - ts > ttl_sec]
    for tok in stale:
        _USED_APPROVAL_TOKENS.pop(tok, None)


def _minion_rate_limit_status(rate_per_min: int) -> tuple[bool, int]:
    if rate_per_min <= 0:
        return False, 0

    user_id = str(getattr(current_user, "id", "anon"))
    now_ts = time.time()
    window_start = now_ts - 60.0
    recent = [ts for ts in _MINION_RATE_BUCKETS.get(user_id, []) if ts >= window_start]

    if len(recent) >= rate_per_min:
        oldest_relevant = min(recent)
        retry_after = max(1, int((oldest_relevant + 60.0) - now_ts))
        _MINION_RATE_BUCKETS[user_id] = recent
        return True, retry_after

    recent.append(now_ts)
    _MINION_RATE_BUCKETS[user_id] = recent
    return False, 0


def _apex_chat_fallback(prompt: str, reason: str) -> str:
    snippet = prompt[:220] + ("..." if len(prompt) > 220 else "")
    return (
        "AI assistant is temporarily unavailable, so this is a safe fallback response. "
        f"Reason: {reason}.\n\n"
        f"Request received: {snippet}"
    )


def _audit_approval_event(event_name: str, prompt: str, tool: str, reason: str | None = None) -> None:
    prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    metadata = {
        "user_id": getattr(current_user, "id", None),
        "organization_id": getattr(current_user, "organization_id", None),
        "tool": tool,
        "prompt_sha256": prompt_digest,
        "reason": reason,
        "at_utc": datetime.now(timezone.utc).isoformat(),
    }
    current_app.logger.info(
        "Approval token event",
        extra={
            "event_id": f"ai.approval_token.{event_name}",
            "extra_fields": metadata,
        },
    )
    try:
        log_event(
            _audit_db_path(),
            actor=str(getattr(current_user, "username", "unknown")),
            action=f"minion_approval_token_{event_name}",
            entity="ai",
            metadata=metadata,
        )
    except Exception as exc:
        current_app.logger.warning("Could not append audit event for approval token: %s", exc)


def _mint_approval_token(prompt: str, tool: str, user_id: Any, organization_id: Any) -> str:
    serializer = _approval_serializer()
    nonce = uuid.uuid4().hex
    token_data = {
        "tool": str(tool),
        "fp": _approval_fingerprint(prompt, tool, user_id, organization_id),
        "nonce": nonce,
    }
    token = str(serializer.dumps(token_data))
    _audit_approval_event("issued", prompt=prompt, tool=str(tool))
    return token


def _verify_approval_token(token: str, prompt: str, tool: str, user_id: Any, organization_id: Any) -> bool:
    if not token:
        _audit_approval_event("rejected", prompt=prompt, tool=str(tool), reason="missing")
        return False
    if token in _USED_APPROVAL_TOKENS:
        _audit_approval_event("rejected", prompt=prompt, tool=str(tool), reason="replay")
        return False

    ttl = int(current_app.config.get("MINION_APPROVAL_TOKEN_TTL_SEC", _APPROVAL_TOKEN_TTL_SEC))
    serializer = _approval_serializer()
    try:
        data = serializer.loads(token, max_age=ttl)
    except (BadSignature, SignatureExpired):
        _audit_approval_event("rejected", prompt=prompt, tool=str(tool), reason="invalid_or_expired")
        return False

    expected_fp = _approval_fingerprint(prompt, tool, user_id, organization_id)
    token_tool = str(data.get("tool", ""))
    token_fp = str(data.get("fp", ""))
    if token_tool != str(tool) or token_fp != expected_fp:
        _audit_approval_event("rejected", prompt=prompt, tool=str(tool), reason="mismatch")
        return False

    now_ts = time.time()
    _prune_used_approval_tokens(now_ts, ttl)
    _USED_APPROVAL_TOKENS[token] = now_ts
    _audit_approval_event("verified", prompt=prompt, tool=str(tool))
    return True


def _audit_db_path() -> str:
    uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///ngo_homesuite.db"))
    if uri.startswith("sqlite:///"):
        return uri.replace("sqlite:///", "", 1)
    return "ngo_homesuite.db"


def _resolve_session_id() -> str:
    user_part = str(getattr(current_user, "id", "anon"))
    expected_prefix = f"web-{user_part}-"
    existing = str(session.get(_SESSION_KEY, "")).strip()
    if existing.startswith(expected_prefix):
        return existing

    generated = f"web-{user_part}-{uuid.uuid4().hex[:8]}"
    session[_SESSION_KEY] = generated
    session.modified = True
    return generated


def _reset_session_id() -> None:
    session.pop(_SESSION_KEY, None)
    session.modified = True


def _resolve_tenant_id(payload: dict[str, Any]) -> str:
    configured_default = str(current_app.config.get("APEX_TENANT_ID", "ngo-default"))
    requested_tenant = str(payload.get("tenant_id") or "").strip()
    user_org_id = getattr(current_user, "organization_id", None)

    if user_org_id is None:
        return requested_tenant or configured_default

    canonical_tenant = str(user_org_id)
    if requested_tenant and requested_tenant != canonical_tenant:
        raise PermissionError("tenant_id must match authenticated user's organization_id")
    return canonical_tenant


def _get_or_create_conversation(session_id: str, model: str, tenant_id: str) -> AIConversation:
    user_id = getattr(current_user, "id", None)
    organization_id = getattr(current_user, "organization_id", None)

    conv = db.session.scalars(
        select(AIConversation).where(AIConversation.session_id == session_id).limit(1)
    ).first()
    if conv is not None:
        # Prevent cross-tenant or cross-user conversation reuse if a session id is ever replayed.
        if conv.organization_id != organization_id or conv.user_id != user_id:
            raise PermissionError("Conversation does not belong to the authenticated user context")
        return conv

    if conv is None:
        conv = AIConversation(
            session_id=session_id,
            user_id=user_id,
            organization_id=organization_id,
            model=model,
            tenant_id=tenant_id,
        )
        db.session.add(conv)
        db.session.flush()  # get conv.id without committing
    return conv


def _get_existing_conversation(session_id: str) -> AIConversation | None:
    user_id = getattr(current_user, "id", None)
    organization_id = getattr(current_user, "organization_id", None)
    conv = db.session.scalars(
        select(AIConversation).where(AIConversation.session_id == session_id).limit(1)
    ).first()
    if conv is None:
        return None
    if conv.organization_id != organization_id or conv.user_id != user_id:
        raise PermissionError("Conversation does not belong to the authenticated user context")
    return conv


def _persist_exchange(session_id: str, model: str, tenant_id: str,
                      user_prompt: str, assistant_reply: str, prompt_digest: str) -> None:
    """Persist user+assistant messages.  Best-effort: errors are logged, not raised."""
    try:
        conv = _get_or_create_conversation(session_id, model, tenant_id)
        db.session.add(AIMessage(
            conversation_id=conv.id,
            role="user",
            content=user_prompt,
            prompt_sha256=prompt_digest,
        ))
        db.session.add(AIMessage(
            conversation_id=conv.id,
            role="assistant",
            content=assistant_reply,
        ))
        max_messages = int(current_app.config.get("MINION_CONVERSATION_MAX_MESSAGES", 200))
        if max_messages > 0:
            message_ids = list(
                db.session.scalars(
                    select(AIMessage.id)
                    .where(AIMessage.conversation_id == conv.id)
                    .order_by(AIMessage.id.desc())
                    .offset(max_messages)
                )
            )
            if message_ids:
                db.session.connection().exec_driver_sql(
                    str(delete(AIMessage).where(AIMessage.id.in_(message_ids)).compile(compile_kwargs={"literal_binds": True}))
                )
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        current_app.logger.warning("AI persistence failed: %s", exc)


def _client() -> OllamaClient:
    return OllamaClient(
        host=current_app.config.get("OLLAMA_HOST", current_app.config.get("APEX_BASE_URL", "http://localhost:11434")),
        model=str(current_app.config.get("OLLAMA_MODEL", current_app.config.get("APEX_MODEL", "llama3.2"))),
        timeout_s=float(current_app.config.get("OLLAMA_TIMEOUT_S", current_app.config.get("APEX_TIMEOUT_S", 120.0))),
    )


def _build_context_preamble(context: dict) -> str:
    """Build a short data-context header to prepend to the user prompt.

    This lets the model give grounded, specific answers (e.g. "you have 47 donors")
    without the user having to repeat that information in every message.
    """
    if not context:
        return ""
    parts: list[str] = []
    if context.get("organization"):
        parts.append(f"Organization: {context['organization']}")
    page = context.get("page") or context.get("active_page") or ""
    if page:
        parts.append(f"Page: {page}")
    _money_keys = {
        "total_donations": "Total donations",
        "total_expenses": "Total expenses",
        "net_balance": "Net balance",
    }
    _count_keys = {
        "donor_count": "Donors",
        "donation_count": "Donations listed",
        "expense_count": "Expenses listed",
        "project_count": "Active projects",
        "total_funds": "Active funds",
        "fund_count": "Funds listed",
    }
    for key, label in _money_keys.items():
        val = context.get(key)
        if val is not None:
            parts.append(f"{label}: ${float(val):,.2f}")
    for key, label in _count_keys.items():
        val = context.get(key)
        if val is not None:
            parts.append(f"{label}: {val}")
    if not parts:
        return ""
    return "[Data context: " + " | ".join(parts) + "]\n\n"


def _audit_interaction(user_prompt: str, model: str, tenant_id: str) -> None:
    digest = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
    current_app.logger.info(
        "AI interaction",
        extra={
            "event_id": "ai.interaction",
            "extra_fields": {
                "user_id": getattr(current_user, "id", None),
                "tenant_id": tenant_id,
                "model": model,
                "prompt_sha256": digest,
                "at_utc": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
    try:
        log_event(
            _audit_db_path(),
            actor=str(getattr(current_user, "username", "unknown")),
            action="minion_query",
            entity="ai",
            metadata={
                "user_id": getattr(current_user, "id", None),
                "tenant_id": tenant_id,
                "model": model,
                "prompt_sha256": digest,
            },
        )
    except Exception as exc:
        current_app.logger.warning("Could not append audit event for AI interaction: %s", exc)


@ai_bp.route("/history", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def conversation_history() -> Response:
    """Return the last 20 AI conversations for the current user."""
    user_id = getattr(current_user, "id", None)
    organization_id = getattr(current_user, "organization_id", None)
    convs = (
        AIConversation.query
        .filter_by(user_id=user_id, organization_id=organization_id)
        .order_by(AIConversation.created_at.desc())
        .limit(20)
        .all()
    )
    return jsonify([
        {
            "session_id": c.session_id,
            "model": c.model,
            "created_at": c.created_at.isoformat(),
            "message_count": len(c.messages),
            "preview": next((m.content[:160] for m in c.messages if m.role == "user"), ""),
        }
        for c in convs
    ])


@ai_bp.route("/conversation/current", methods=["GET"])
@login_required
@roles_required("admin", "staff", "viewer")
def current_conversation() -> Response:
    session_id = _resolve_session_id()
    conv = _get_existing_conversation(session_id)
    if conv is None:
        return jsonify({"session_id": session_id, "messages": [], "model": None})

    return jsonify(
        {
            "session_id": session_id,
            "model": conv.model,
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in conv.messages
            ],
        }
    )


@ai_bp.route("/conversation/reset", methods=["POST"])
@login_required
@roles_required("admin", "staff", "viewer")
def reset_conversation() -> Response:
    _reset_session_id()
    return jsonify({"ok": True, "session_id": _resolve_session_id()})


@ai_bp.route("/health", methods=["GET"])
@login_required
def health() -> Response:
    return jsonify(
        {
            "ok": bool(current_app.config.get("APEX_AI_ENABLED", False)),
            "provider": "apex-sovereign",
            "base_url": current_app.config.get("APEX_BASE_URL"),
        }
    )


@ai_bp.route("/chat", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def chat_once() -> Response:
    if not current_app.config.get("APEX_AI_ENABLED", False):
        return jsonify({"error": "AI assistant is disabled."}), 503

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    model = str(payload.get("model") or current_app.config.get("APEX_MODEL", "llama3.2"))
    try:
        tenant_id = _resolve_tenant_id(payload)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    session_id = _resolve_session_id()

    redacted_prompt, n_redacted = redact_pii(prompt)
    if n_redacted:
        current_app.logger.info("PII redacted from /ai/chat prompt (%d replacements)", n_redacted)

    _audit_interaction(redacted_prompt, model, tenant_id)
    digest = hashlib.sha256(redacted_prompt.encode("utf-8")).hexdigest()

    try:
        answer = _client().query(
            prompt=_build_context_preamble(context) + redacted_prompt,
            context=context,
            model=model,
            tenant_id=tenant_id,
            session_id=session_id,
            system_prompt=NGO_APEX_POLICY_SYSTEM_PROMPT,
        )
    except OllamaClientError as exc:
        fallback = _apex_chat_fallback(redacted_prompt, str(exc))
        _persist_exchange(session_id, model, tenant_id, redacted_prompt, fallback, digest)
        return jsonify({"response": fallback, "mode": "fallback"})

    _persist_exchange(session_id, model, tenant_id, redacted_prompt, answer, digest)

    return jsonify({"response": answer, "mode": "normal"})


@ai_bp.route("/stream", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def stream_chat() -> Response:
    if not current_app.config.get("APEX_AI_ENABLED", False):
        return Response("data: {\"error\":\"AI assistant is disabled.\"}\n\n", mimetype="text/event-stream", status=503)

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return Response("data: {\"error\":\"Prompt is required.\"}\n\n", mimetype="text/event-stream", status=400)

    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    model = str(payload.get("model") or current_app.config.get("APEX_MODEL", "llama3.2"))
    try:
        tenant_id = _resolve_tenant_id(payload)
    except PermissionError as exc:
        return Response(
            f"data: {json.dumps({'error': str(exc)})}\n\n",
            mimetype="text/event-stream",
            status=403,
        )
    session_id = _resolve_session_id()

    redacted_prompt, n_redacted = redact_pii(prompt)
    if n_redacted:
        current_app.logger.info("PII redacted from /ai/stream prompt (%d replacements)", n_redacted)
    digest = hashlib.sha256(redacted_prompt.encode("utf-8")).hexdigest()

    _audit_interaction(redacted_prompt, model, tenant_id)

    full_prompt = _build_context_preamble(context) + redacted_prompt

    app = current_app._get_current_object()  # needed inside generator

    def event_stream() -> Any:
        tokens: list[str] = []
        try:
            for token in _client().stream_query(
                prompt=full_prompt,
                context=context,
                model=model,
                tenant_id=tenant_id,
                session_id=session_id,
                system_prompt=NGO_APEX_POLICY_SYSTEM_PROMPT,
            ):
                tokens.append(token)
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"
            # Persist after stream completes
            with app.app_context():
                _persist_exchange(
                    session_id, model, tenant_id,
                    redacted_prompt, "".join(tokens), digest,
                )
        except OllamaClientError as exc:
            with app.app_context():
                partial = "".join(tokens)
                fallback = _apex_chat_fallback(redacted_prompt, str(exc))
                persisted = partial if partial else fallback
                _persist_exchange(
                    session_id, model, tenant_id,
                    redacted_prompt, persisted, digest,
                )
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            yield "data: [DONE]\n\n"

    response = Response(stream_with_context(event_stream()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@ai_bp.route("/minion/chat", methods=["POST"])
@login_required
@roles_required("admin", "staff", "viewer")
def minion_chat() -> Response:
    if not current_app.config.get("MINION_ENABLED", True):
        return jsonify({"error": "HomeSuite Minion is disabled."}), 503

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    if bool(current_app.config.get("RATELIMIT_ENABLED", True)):
        limited, retry_after = _minion_rate_limit_status(
            int(current_app.config.get("MINION_RATE_LIMIT_PER_MIN", 30))
        )
        if limited:
            response = jsonify(
                {
                    "error": "Minion rate limit exceeded. Please retry shortly.",
                    "retry_after_sec": retry_after,
                }
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response

    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    model = str(payload.get("model") or current_app.config.get("OLLAMA_MODEL", "llama3.2"))
    try:
        tenant_id = _resolve_tenant_id(payload)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    session_id = _resolve_session_id()

    # Only admin/staff can execute action tools; viewer is read-only.
    role = str(getattr(current_user, "role", "viewer"))
    allow_actions = bool(payload.get("allow_actions", False)) and role in {"admin", "staff"}
    use_web = bool(payload.get("use_web", False))
    approved_action_items = _parse_approved_actions(payload.get("approved_actions"))
    route_allowlist_in_payload = "tool_allowlist" in payload
    route_allowlist = _parse_tool_list(payload.get("tool_allowlist"))
    config_allowlist = _parse_tool_list(current_app.config.get("MINION_TOOL_ALLOWLIST", ""))

    if route_allowlist_in_payload:
        if config_allowlist:
            config_allow = set(config_allowlist)
            tool_allowlist = [name for name in route_allowlist if name in config_allow]
        else:
            tool_allowlist = route_allowlist
    else:
        tool_allowlist = config_allowlist or None

    if tool_allowlist is not None:
        allowed_now = set(tool_allowlist)
        approved_action_items = [item for item in approved_action_items if item["tool"] in allowed_now]

    require_token = bool(current_app.config.get("MINION_REQUIRE_APPROVAL_TOKEN", True))
    approved_actions: list[str] = []
    for item in approved_action_items:
        tool = item["tool"]
        token = item.get("token", "")
        if require_token:
            if _verify_approval_token(
                token=token,
                prompt=prompt,
                tool=tool,
                user_id=getattr(current_user, "id", None),
                organization_id=getattr(current_user, "organization_id", None),
            ):
                approved_actions.append(tool)
        else:
            approved_actions.append(tool)

    _audit_interaction(prompt, model, tenant_id)

    minion = HomeSuiteMinion.from_app()
    response = minion.answer(
        prompt=prompt,
        context=context,
        runtime_ctx={
            "actor": getattr(current_user, "username", "minion"),
            "organization_id": getattr(current_user, "organization_id", None),
            "user_id": getattr(current_user, "id", None),
            "approved_actions": approved_actions,
            "tool_allowlist": tool_allowlist,
            "allow_web_tools": bool(current_app.config.get("MINION_ALLOW_WEB_TOOLS", False)),
            "tool_timeout_sec": float(current_app.config.get("MINION_TOOL_TIMEOUT_SEC", 8.0)),
        },
        allow_actions=allow_actions,
        use_web=use_web,
    )

    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    _persist_exchange(session_id, model, tenant_id, prompt, response.answer, digest)

    for action in response.actions:
        if action.get("status") == "pending_approval" and action.get("tool"):
            action["approval_token"] = _mint_approval_token(
                prompt=prompt,
                tool=str(action.get("tool")),
                user_id=getattr(current_user, "id", None),
                organization_id=getattr(current_user, "organization_id", None),
            )

    return jsonify(
        {
            "response": response.answer,
            "sources": response.sources,
            "actions": response.actions,
            "redactions": response.redactions,
            "mode": "minion",
        }
    )


@ai_bp.route("/minion/reindex", methods=["POST"])
@login_required
@roles_required("admin")
def minion_reindex() -> Response:
    if not current_app.config.get("MINION_ENABLED", True):
        return jsonify({"error": "HomeSuite Minion is disabled."}), 503

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    user_summaries = payload.get("user_summaries") if isinstance(payload.get("user_summaries"), list) else []
    user_summaries = [str(s) for s in user_summaries if isinstance(s, str)]

    minion = HomeSuiteMinion.from_app()
    total_chunks = minion.reindex(user_summary_texts=user_summaries)

    try:
        log_event(
            _audit_db_path(),
            actor=str(getattr(current_user, "username", "unknown")),
            action="minion_reindex",
            entity="ai",
            metadata={
                "chunks": total_chunks,
                "user_summaries": len(user_summaries),
            },
        )
    except Exception as exc:
        current_app.logger.warning("Could not append audit event for reindex: %s", exc)

    return jsonify({"ok": True, "chunks_indexed": total_chunks})

