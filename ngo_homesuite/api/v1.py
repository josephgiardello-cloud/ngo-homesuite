from __future__ import annotations

from flask import Blueprint, current_app, request
from flask_login import current_user, login_required
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from ngo_homesuite.app.container import AppContainer
from ngo_homesuite.app.write_gate import WriteGateExecutionError
from ngo_homesuite.models.core import Campaign, Donation, Donor, db
from ngo_homesuite.observability import InMemoryMetrics
from ngo_homesuite.shared_kernel import redact_payload
from ngo_homesuite.tenant import TenantContext


api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


class WorkflowCreateRequest(BaseModel):
    org_id: str
    workflow_type: str


class WorkflowEventRequest(BaseModel):
    org_id: str
    event_type: str
    actor_id: str | None = None
    role: str | None = None
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
    try:
        instance = _container().create_workflow_instance(org_id=body.org_id, workflow_type=body.workflow_type)
    except WriteGateExecutionError as exc:
        return {
            "error": str(exc),
            "execution_error": exc.to_dict(),
        }, exc.http_status
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

    auth_user_id = str(getattr(current_user, "id", ""))
    auth_role = _map_role(str(getattr(current_user, "role", "viewer")))
    requested_actor = str(body.actor_id or "").strip()
    requested_role = _map_role(str(body.role or "").strip()) if body.role else ""

    if requested_actor and auth_user_id and requested_actor != auth_user_id:
        return {"error": "actor_id must match authenticated user."}, 403
    if requested_role and requested_role != auth_role:
        return {"error": "role must match authenticated user role."}, 403

    tenant = TenantContext(
        org_id=body.org_id,
        user_id=auth_user_id or requested_actor or "unknown",
        role=auth_role,
    )
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
    except WriteGateExecutionError as exc:
        return {
            "error": str(exc),
            "execution_error": exc.to_dict(),
        }, exc.http_status

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
    user_org_id = getattr(current_user, "organization_id", None)
    if user_org_id is not None:
        scoped_instance = _container().workflow_repository.get(instance_id, org_id=str(user_org_id))
        if scoped_instance is None:
            return {"error": "Trace not found."}, 404

    trace = _container().tracer.get(instance_id)
    if trace is None:
        return {"error": "Trace not found."}, 404
    return {
        "ok": True,
        "trace": {
            "workflow_instance_id": trace.workflow_instance_id,
            "org_id": trace.org_id,
            "started_at": trace.started_at,
            "steps": trace.steps,
        },
    }


@api_v1_bp.get("/metrics")
@login_required
def metrics_snapshot():
    role = _map_role(getattr(current_user, "role", ""))
    if role != "org_admin":
        return {"error": "Insufficient permissions to view metrics."}, 403
    metrics = current_app.extensions.get("metrics")
    if not isinstance(metrics, InMemoryMetrics):
        return {"error": "Metrics subsystem unavailable."}, 503

    org_id = getattr(current_user, "organization_id", None)
    if org_id is not None:
        active_donors = int(
            db.session.scalar(
                    select(func.count(Donor.id)).where(Donor.organization_id == int(org_id))
            )
            or 0
        )
        donation_volume = float(
            db.session.scalar(
                select(func.coalesce(func.sum(Donation.amount), 0.0)).where(
                    Donation.organization_id == int(org_id),
                    Donation.status.in_(("received", "processed", "receipted")),
                )
            )
            or 0.0
        )
        donation_failures = int(
            db.session.scalar(
                select(func.count(Donation.id)).where(
                    Donation.organization_id == int(org_id),
                    Donation.status == "failed",
                )
            )
            or 0
        )
        active_campaigns = int(
            db.session.scalar(
                select(func.count(Campaign.id)).where(
                    Campaign.organization_id == int(org_id),
                    Campaign.status == "active",
                )
            )
            or 0
        )

        metrics.set("ngo_homesuite_active_donors", float(active_donors))
        metrics.set("ngo_homesuite_donation_volume_total", donation_volume)
        metrics.set("ngo_homesuite_active_campaigns", float(active_campaigns))
        metrics.set("ngo_homesuite_donation_failures_total", float(donation_failures))

        campaign_rows = db.session.execute(
            select(Campaign.id, Campaign.raised_amount).where(Campaign.organization_id == int(org_id))
        ).all()
        for campaign_id, raised_amount in campaign_rows:
            metrics.set(
                "donations_total",
                float(raised_amount or 0.0),
                labels={"campaign": str(int(campaign_id))},
            )

    return current_app.response_class(metrics.render_prometheus(), mimetype="text/plain")


@api_v1_bp.get("/audit/events")
@login_required
def list_audit_events():
    org_id = str(request.args.get("org_id", "")).strip()
    include_control = str(request.args.get("include_control", "")).strip().lower() in {"1", "true", "yes"}
    if not org_id:
        return {"error": "org_id query parameter is required."}, 400
    try:
        _enforce_user_org_access(org_id)
    except PermissionError as exc:
        return {"error": str(exc)}, 403
    events = _container().event_store.list_events(org_id=org_id)
    if not include_control:
        events = [event for event in events if event.aggregate_type != "workflow_command"]
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


@api_v1_bp.get("/workflows/instances/<instance_id>/executions")
@login_required
def list_workflow_instance_executions(instance_id: str):
    org_id = str(request.args.get("org_id", "")).strip()
    if not org_id:
        return {"error": "org_id query parameter is required."}, 400
    try:
        _enforce_user_org_access(org_id)
    except PermissionError as exc:
        return {"error": str(exc)}, 403

    events = _container().event_store.list_events(org_id=org_id)
    execution_events: list[dict] = []
    for event in events:
        if event.aggregate_type != "workflow_command":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("instance_id", "")) != str(instance_id):
            continue
        execution_events.append(
            {
                "execution_id": event.aggregate_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "action": payload.get("action"),
                "result": payload.get("result"),
                "retryable": ((payload.get("error") or {}).get("retryable") if isinstance(payload.get("error"), dict) else None),
                "error": payload.get("error"),
            }
        )

    return {
        "ok": True,
        "instance_id": instance_id,
        "org_id": org_id,
        "executions": execution_events,
    }
