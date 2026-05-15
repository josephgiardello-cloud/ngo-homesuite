from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from ngo_homesuite.ai.apex_client import ApexClient, ApexClientError
from ngo_homesuite.ai.pii_redact import redact_pii
from ngo_homesuite.models.core import db, AIConversation, AIMessage
from ngo_homesuite.prompts import NGO_APEX_POLICY_SYSTEM_PROMPT
from ngo_homesuite.web.rbac import roles_required


ai_bp = Blueprint("ai", __name__, url_prefix="/ai")


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


def _client() -> ApexClient:
    return ApexClient(
        base_url=current_app.config["APEX_BASE_URL"],
        api_token=current_app.config.get("APEX_API_TOKEN"),
        timeout_s=float(current_app.config.get("APEX_TIMEOUT_S", 120.0)),
    )


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
            prompt=redacted_prompt,
            context=context,
            model=model,
            tenant_id=tenant_id,
            session_id=session_id,
            system_prompt=NGO_APEX_POLICY_SYSTEM_PROMPT,
        )
    except ApexClientError as exc:
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

    app = current_app._get_current_object()  # needed inside generator

    def event_stream() -> Any:
        tokens: list[str] = []
        try:
            for token in _client().stream_query(
                prompt=redacted_prompt,
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
        except ApexClientError as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    response = Response(stream_with_context(event_stream()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response
