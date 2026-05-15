from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from ngo_homesuite.ai.apex_client import OllamaClient, OllamaClientError
from ngo_homesuite.ai.copilot_service import HomeSuiteCopilot
from ngo_homesuite.ai.pii_redact import redact_pii
from ngo_homesuite.db.audit_log import log_event
from ngo_homesuite.models.core import db, AIConversation, AIMessage
from ngo_homesuite.prompts import NGO_APEX_POLICY_SYSTEM_PROMPT
from ngo_homesuite.web.rbac import roles_required


ai_bp = Blueprint("ai", __name__, url_prefix="/ai")


def _parse_tool_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    return []


def _audit_db_path() -> str:
    uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///ngo_homesuite.db"))
    if uri.startswith("sqlite:///"):
        return uri.replace("sqlite:///", "", 1)
    return "ngo_homesuite.db"


def _resolve_session_id() -> str:
    user_part = str(getattr(current_user, "id", "anon"))
    return f"web-{user_part}-{uuid.uuid4().hex[:8]}"


def _get_or_create_conversation(session_id: str, model: str, tenant_id: str) -> AIConversation:
    conv = AIConversation.query.filter_by(session_id=session_id).first()
    if conv is None:
        conv = AIConversation(
            session_id=session_id,
            user_id=getattr(current_user, "id", None),
            organization_id=getattr(current_user, "organization_id", None),
            model=model,
            tenant_id=tenant_id,
        )
        db.session.add(conv)
        db.session.flush()  # get conv.id without committing
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
            action="copilot_query",
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
    convs = (
        AIConversation.query
        .filter_by(user_id=user_id)
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
        }
        for c in convs
    ])


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
    tenant_id = str(payload.get("tenant_id") or current_app.config.get("APEX_TENANT_ID", "ngo-default"))
    session_id = _resolve_session_id()

    redacted_prompt, n_redacted = redact_pii(prompt)
    if n_redacted:
        current_app.logger.info("PII redacted from /ai/chat prompt (%d replacements)", n_redacted)

    _audit_interaction(redacted_prompt, model, tenant_id)

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
        return jsonify({"error": str(exc)}), 502

    digest = hashlib.sha256(redacted_prompt.encode("utf-8")).hexdigest()
    _persist_exchange(session_id, model, tenant_id, redacted_prompt, answer, digest)

    return jsonify({"response": answer})


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
    tenant_id = str(payload.get("tenant_id") or current_app.config.get("APEX_TENANT_ID", "ngo-default"))
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
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    response = Response(stream_with_context(event_stream()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@ai_bp.route("/copilot/chat", methods=["POST"])
@login_required
@roles_required("admin", "staff", "viewer")
def copilot_chat() -> Response:
    if not current_app.config.get("COPILOT_ENABLED", True):
        return jsonify({"error": "HomeSuite Copilot is disabled."}), 503

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    model = str(payload.get("model") or current_app.config.get("OLLAMA_MODEL", "llama3.2"))
    tenant_id = str(payload.get("tenant_id") or current_app.config.get("APEX_TENANT_ID", "ngo-default"))
    session_id = _resolve_session_id()

    # Only admin/staff can execute action tools; viewer is read-only.
    role = str(getattr(current_user, "role", "viewer"))
    allow_actions = bool(payload.get("allow_actions", False)) and role in {"admin", "staff"}
    use_web = bool(payload.get("use_web", False))
    approved_actions = _parse_tool_list(payload.get("approved_actions"))
    route_allowlist_in_payload = "tool_allowlist" in payload
    route_allowlist = _parse_tool_list(payload.get("tool_allowlist"))
    config_allowlist = _parse_tool_list(current_app.config.get("COPILOT_TOOL_ALLOWLIST", ""))

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
        approved_actions = [name for name in approved_actions if name in allowed_now]

    _audit_interaction(prompt, model, tenant_id)

    copilot = HomeSuiteCopilot.from_app()
    response = copilot.answer(
        prompt=prompt,
        context=context,
        runtime_ctx={
            "actor": getattr(current_user, "username", "copilot"),
            "organization_id": getattr(current_user, "organization_id", None),
            "user_id": getattr(current_user, "id", None),
            "approved_actions": approved_actions,
            "tool_allowlist": tool_allowlist,
        },
        allow_actions=allow_actions,
        use_web=use_web,
    )

    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    _persist_exchange(session_id, model, tenant_id, prompt, response.answer, digest)

    return jsonify(
        {
            "response": response.answer,
            "sources": response.sources,
            "actions": response.actions,
            "redactions": response.redactions,
            "mode": "copilot",
        }
    )


@ai_bp.route("/copilot/reindex", methods=["POST"])
@login_required
@roles_required("admin")
def copilot_reindex() -> Response:
    if not current_app.config.get("COPILOT_ENABLED", True):
        return jsonify({"error": "HomeSuite Copilot is disabled."}), 503

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    user_summaries = payload.get("user_summaries") if isinstance(payload.get("user_summaries"), list) else []
    user_summaries = [str(s) for s in user_summaries if isinstance(s, str)]

    copilot = HomeSuiteCopilot.from_app()
    total_chunks = copilot.reindex(user_summary_texts=user_summaries)

    try:
        log_event(
            _audit_db_path(),
            actor=str(getattr(current_user, "username", "unknown")),
            action="copilot_reindex",
            entity="ai",
            metadata={
                "chunks": total_chunks,
                "user_summaries": len(user_summaries),
            },
        )
    except Exception as exc:
        current_app.logger.warning("Could not append audit event for reindex: %s", exc)

    return jsonify({"ok": True, "chunks_indexed": total_chunks})
