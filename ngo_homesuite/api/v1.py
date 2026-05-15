from __future__ import annotations

from flask import Blueprint, current_app, request
from flask_login import current_user, login_required
from pydantic import BaseModel, Field, ValidationError

from ngo_homesuite.app.container import AppContainer
from ngo_homesuite.shared_kernel import redact_payload
from ngo_homesuite.tenant import TenantContext


api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


class WorkflowCreateRequest(BaseModel):
    org_id: str
    workflow_type: str


class WorkflowEventRequest(BaseModel):
    org_id: str
    event_type: str
    actor_id: str
    role: str
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


def _container() -> AppContainer:
    container = current_app.extensions.get("v2_container")
    if not isinstance(container, AppContainer):
        raise RuntimeError("V2 container is not configured")
    return container


def _map_role(role: str) -> str:
    mapping = {
        "admin": "org_admin",
        "staff": "case_worker",
        "volunteer": "volunteer",
        "viewer": "auditor",
        "org_admin": "org_admin",
        "case_worker": "case_worker",
        "auditor": "auditor",
    }
    return mapping.get(role, role)


def _enforce_user_org_access(request_org_id: str) -> None:
    user_org_id = getattr(current_user, "organization_id", None)
    if user_org_id is None:
        return
    if str(user_org_id) != str(request_org_id):
        raise PermissionError("User is not allowed to access another tenant org_id")


@api_v1_bp.get("/workflows")
@login_required
def list_workflows():
    container = _container()
    return {
        "ok": True,
        "workflow_types": sorted(container.workflow_definitions.keys()),
    }


@api_v1_bp.post("/workflows/instances")
@login_required
def create_workflow_instance():
    try:
        body = WorkflowCreateRequest(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return {"error": str(exc)}, 400

    role = _map_role(getattr(current_user, "role", ""))
    if role not in {"org_admin", "case_worker"}:
        return {"error": "Insufficient permissions to create workflow instances."}, 403
    try:
        _enforce_user_org_access(body.org_id)
    except PermissionError as exc:
        return {"error": str(exc)}, 403

    instance = _container().create_workflow_instance(org_id=body.org_id, workflow_type=body.workflow_type)
    return {
        "ok": True,
        "instance": {
            "instance_id": instance.instance_id,
            "org_id": instance.org_id,
            "workflow_type": instance.workflow_type,
            "current_step": instance.current_step,
            "status": instance.status,
        },
    }


@api_v1_bp.post("/workflows/instances/<instance_id>/events")
@login_required
def apply_workflow_event(instance_id: str):
    try:
        body = WorkflowEventRequest(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return {"error": str(exc)}, 400

    tenant = TenantContext(org_id=body.org_id, user_id=body.actor_id, role=_map_role(body.role))
    try:
        _enforce_user_org_access(body.org_id)
        instance, was_replay = _container().dispatch_workflow_event(
            instance_id=instance_id,
            event_type=body.event_type,
            tenant=tenant,
            payload=redact_payload(body.payload),
            idempotency_key=body.idempotency_key,
        )
    except PermissionError as exc:
        return {"error": str(exc)}, 403
    except (KeyError, ValueError, RuntimeError) as exc:
        return {"error": str(exc)}, 400

    return {
        "ok": True,
        "instance": {
            "instance_id": instance.instance_id,
            "org_id": instance.org_id,
            "workflow_type": instance.workflow_type,
            "current_step": instance.current_step,
            "version": instance.version,
            "status": instance.status,
            "history": instance.history,
            "idempotent_replay": was_replay,
        },
    }


@api_v1_bp.get("/workflows/instances/<instance_id>/trace")
@login_required
def get_workflow_trace(instance_id: str):
    trace = _container().tracer.get(instance_id)
    if trace is None:
        return {"error": "Trace not found."}, 404
    return {
        "ok": True,
        "trace": {
            "workflow_instance_id": trace.workflow_instance_id,
            "org_id": trace.org_id,
            "steps": trace.steps,
        },
    }


@api_v1_bp.get("/audit/events")
@login_required
def list_audit_events():
    org_id = str(request.args.get("org_id", "")).strip()
    if not org_id:
        return {"error": "org_id query parameter is required."}, 400
    try:
        _enforce_user_org_access(org_id)
    except PermissionError as exc:
        return {"error": str(exc)}, 403
    events = _container().event_store.list_events(org_id=org_id)
    return {
        "ok": True,
        "events": [
            {
                "event_id": event.event_id,
                "org_id": event.org_id,
                "event_type": event.event_type,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "actor_id": event.actor_id,
                "payload": redact_payload(event.payload),
                "occurred_at": event.occurred_at,
            }
            for event in events
        ],
    }
