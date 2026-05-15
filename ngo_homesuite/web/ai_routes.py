from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from ngo_homesuite.ai.apex_client import ApexClient, ApexClientError
from ngo_homesuite.prompts import NGO_APEX_POLICY_SYSTEM_PROMPT


ai_bp = Blueprint("ai", __name__, url_prefix="/ai")


def _resolve_session_id() -> str:
    user_part = str(getattr(current_user, "id", "anon"))
    return f"web-{user_part}-{uuid.uuid4().hex[:8]}"


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

    _audit_interaction(prompt, model, tenant_id)

    try:
        answer = _client().query(
            prompt=prompt,
            context=context,
            model=model,
            tenant_id=tenant_id,
            session_id=_resolve_session_id(),
            system_prompt=NGO_APEX_POLICY_SYSTEM_PROMPT,
        )
    except ApexClientError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"response": answer})


@ai_bp.route("/stream", methods=["POST"])
@login_required
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

    _audit_interaction(prompt, model, tenant_id)

    def event_stream() -> Any:
        try:
            for token in _client().stream_query(
                prompt=prompt,
                context=context,
                model=model,
                tenant_id=tenant_id,
                session_id=session_id,
                system_prompt=NGO_APEX_POLICY_SYSTEM_PROMPT,
            ):
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"
        except ApexClientError as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    response = Response(stream_with_context(event_stream()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response
