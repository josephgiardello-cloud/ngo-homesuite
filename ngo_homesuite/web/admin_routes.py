"""Admin routes: org user management, role assignment, org settings.

All endpoints require the 'admin' role.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func, select

from ngo_homesuite.web.auth_routes import require_step_up_auth
from ngo_homesuite.web.rbac import roles_required


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_CUSTOM_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_CUSTOM_FIELD_TYPES = {"text", "number", "date", "boolean", "select"}
_CUSTOM_FIELD_ENTITIES = {"donor", "campaign"}


def _org_id() -> int:
    return int(current_user.organization_id)


def _get_org_custom_field_schema(org: Any) -> dict[str, list[dict[str, Any]]]:
    metadata = org.metadata_json if isinstance(org.metadata_json, dict) else {}
    schema = metadata.get("custom_fields_schema")
    if not isinstance(schema, dict):
        return {"donor": [], "campaign": []}

    normalized: dict[str, list[dict[str, Any]]] = {"donor": [], "campaign": []}
    for entity in _CUSTOM_FIELD_ENTITIES:
        items = schema.get(entity, [])
        if isinstance(items, list):
            normalized[entity] = [row for row in items if isinstance(row, dict)]
    return normalized


def _validate_custom_field_schema(raw_schema: Any) -> tuple[dict[str, list[dict[str, Any]]] | None, str | None]:
    if not isinstance(raw_schema, dict):
        return None, "schema must be an object"

    normalized: dict[str, list[dict[str, Any]]] = {"donor": [], "campaign": []}
    for entity, raw_fields in raw_schema.items():
        if entity not in _CUSTOM_FIELD_ENTITIES:
            return None, f"Unsupported entity '{entity}'. Allowed entities: {sorted(_CUSTOM_FIELD_ENTITIES)}"
        if not isinstance(raw_fields, list):
            return None, f"{entity} must be a list"
        if len(raw_fields) > 50:
            return None, f"{entity} exceeds max field count (50)"

        seen_keys: set[str] = set()
        for idx, raw_field in enumerate(raw_fields):
            if not isinstance(raw_field, dict):
                return None, f"{entity}[{idx}] must be an object"

            key = str(raw_field.get("key") or "").strip().lower()
            label = str(raw_field.get("label") or "").strip()
            field_type = str(raw_field.get("type") or "").strip().lower()
            required = bool(raw_field.get("required", False))

            if not _CUSTOM_FIELD_KEY_RE.match(key):
                return None, f"{entity}[{idx}].key must match pattern {_CUSTOM_FIELD_KEY_RE.pattern}"
            if key in seen_keys:
                return None, f"{entity} has duplicate key '{key}'"
            seen_keys.add(key)

            if not label:
                return None, f"{entity}[{idx}].label is required"
            if len(label) > 120:
                return None, f"{entity}[{idx}].label must be <= 120 characters"
            if field_type not in _CUSTOM_FIELD_TYPES:
                return None, f"{entity}[{idx}].type must be one of {sorted(_CUSTOM_FIELD_TYPES)}"

            options: list[str] = []
            if field_type == "select":
                raw_options = raw_field.get("options")
                if not isinstance(raw_options, list) or not raw_options:
                    return None, f"{entity}[{idx}].options is required for select fields"
                for option in raw_options:
                    text = str(option or "").strip()
                    if not text:
                        return None, f"{entity}[{idx}].options cannot contain empty values"
                    options.append(text)

            normalized[entity].append(
                {
                    "key": key,
                    "label": label,
                    "type": field_type,
                    "required": required,
                    "options": options,
                }
            )

    return normalized, None


# ---------------------------------------------------------------------------
# User management within org
# ---------------------------------------------------------------------------

@admin_bp.get("/users")
@login_required
@roles_required("admin")
def list_org_users_route():
    """List all users in the current org."""
    from ngo_homesuite.models.core import User, db

    stmt = select(User).where(User.organization_id == _org_id()).order_by(User.created_at.asc())
    users = list(db.session.scalars(stmt))
    return jsonify([
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "role": u.role,
            "is_active": u.is_active,
            "can_authorize_external_comms": bool(u.can_authorize_external_comms),
            "effective_external_comms_authority": bool(u.role == "admin" or u.can_authorize_external_comms),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in users
    ])


@admin_bp.get("/users/<int:user_id>")
@login_required
@roles_required("admin")
def get_org_user_route(user_id: int):
    from ngo_homesuite.models.core import User, db

    stmt = select(User).where(User.id == user_id, User.organization_id == _org_id()).limit(1)
    user = db.session.scalars(stmt).first()
    if user is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": user.role,
        "is_active": user.is_active,
        "can_authorize_external_comms": bool(user.can_authorize_external_comms),
        "effective_external_comms_authority": bool(user.role == "admin" or user.can_authorize_external_comms),
        "phone": user.phone,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    })


ALLOWED_ROLES = {"admin", "staff", "volunteer", "viewer"}


@admin_bp.patch("/users/<int:user_id>/role")
@login_required
@roles_required("admin")
@require_step_up_auth
def update_user_role_route(user_id: int):
    """Update a user's role within the org.

    Body: {"role": "staff"}
    An admin cannot demote themselves.
    """
    from ngo_homesuite.models.core import User, db

    if user_id == current_user.id:
        return jsonify({"error": "Cannot change your own role"}), 400

    data = request.get_json(silent=True) or {}
    new_role = (data.get("role") or "").strip().lower()
    if new_role not in ALLOWED_ROLES:
        return jsonify({"error": f"role must be one of {sorted(ALLOWED_ROLES)}"}), 400

    stmt = select(User).where(User.id == user_id, User.organization_id == _org_id()).limit(1)
    user = db.session.scalars(stmt).first()
    if user is None:
        return jsonify({"error": "not found"}), 404

    previous_role = str(user.role or "").strip().lower()
    user.role = new_role
    db.session.commit()

    # Best-effort security audit: role changes are sensitive admin operations.
    try:
        from ngo_homesuite.audit.security_events import SecurityAuditService, SecurityEventType

        SecurityAuditService.log_event(
            event_type=SecurityEventType.ROLE_ASSIGNED,
            action=f"user_role_changed_{previous_role}_to_{new_role}",
            result="success",
            resource_type="user",
            resource_id=user.id,
            resource_org_id=user.organization_id,
            payload={
                "target_user_id": user.id,
                "target_username": user.username,
                "previous_role": previous_role,
                "new_role": new_role,
                "changed_by_user_id": current_user.id,
            },
        )
    except Exception:
        # Do not fail the role-change operation if audit persistence is unavailable.
        pass

    return jsonify({"id": user.id, "username": user.username, "role": user.role})


@admin_bp.patch("/users/<int:user_id>/status")
@login_required
@roles_required("admin")
@require_step_up_auth
def update_user_status_route(user_id: int):
    """Activate or deactivate a user.

    Body: {"is_active": false}
    """
    from ngo_homesuite.models.core import User, db

    if user_id == current_user.id:
        return jsonify({"error": "Cannot change your own status"}), 400

    data = request.get_json(silent=True) or {}
    if "is_active" not in data:
        return jsonify({"error": "is_active is required"}), 400

    stmt = select(User).where(User.id == user_id, User.organization_id == _org_id()).limit(1)
    user = db.session.scalars(stmt).first()
    if user is None:
        return jsonify({"error": "not found"}), 404

    user.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify({"id": user.id, "is_active": user.is_active})


@admin_bp.patch("/users/<int:user_id>/permissions")
@login_required
@roles_required("admin")
@require_step_up_auth
def update_user_permissions_route(user_id: int):
    """Update fine-grained permission flags for a user in the same org.

    Body: {"can_authorize_external_comms": true}
    """
    from ngo_homesuite.models.core import User, db

    data = request.get_json(silent=True) or {}
    if "can_authorize_external_comms" not in data:
        return jsonify({"error": "can_authorize_external_comms is required"}), 400

    stmt = select(User).where(User.id == user_id, User.organization_id == _org_id()).limit(1)
    user = db.session.scalars(stmt).first()
    if user is None:
        return jsonify({"error": "not found"}), 404

    user.can_authorize_external_comms = bool(data["can_authorize_external_comms"])
    db.session.commit()
    return jsonify(
        {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "can_authorize_external_comms": bool(user.can_authorize_external_comms),
            "effective_external_comms_authority": bool(user.role == "admin" or user.can_authorize_external_comms),
        }
    )


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # Accept ISO timestamps with trailing Z.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


@admin_bp.get("/external-comms/audit")
@login_required
@roles_required("admin")
def list_external_comms_audit_route():
    """Read-only audit list for outbound external communication authorizations."""
    from ngo_homesuite.models.core import ExternalCommunicationAuthorization, db

    campaign_id = request.args.get("campaign_id", type=int)
    user_id = request.args.get("user_id", type=int)
    batch_id = request.args.get("batch_id", type=int)
    channel = (request.args.get("channel") or "").strip().lower()
    communication_type = (request.args.get("communication_type") or "").strip().lower()
    reviewer_name = (request.args.get("reviewer_name") or "").strip()
    limit = max(1, min(int(request.args.get("limit", 50) or 50), 200))

    try:
        authorized_from = _parse_iso_dt(request.args.get("authorized_from"))
        authorized_to = _parse_iso_dt(request.args.get("authorized_to"))
    except ValueError:
        return jsonify({"error": "authorized_from/authorized_to must be valid ISO timestamps"}), 400

    stmt = select(ExternalCommunicationAuthorization).where(
        ExternalCommunicationAuthorization.organization_id == _org_id(),
    )
    if campaign_id:
        stmt = stmt.where(ExternalCommunicationAuthorization.campaign_id == campaign_id)
    if user_id:
        stmt = stmt.where(ExternalCommunicationAuthorization.user_id == user_id)
    if batch_id:
        stmt = stmt.where(ExternalCommunicationAuthorization.batch_id == batch_id)
    if channel:
        stmt = stmt.where(func.lower(ExternalCommunicationAuthorization.channel) == channel)
    if communication_type:
        stmt = stmt.where(func.lower(ExternalCommunicationAuthorization.communication_type) == communication_type)
    if reviewer_name:
        stmt = stmt.where(ExternalCommunicationAuthorization.reviewer_name.ilike(f"%{reviewer_name}%"))
    if authorized_from is not None:
        stmt = stmt.where(ExternalCommunicationAuthorization.authorized_at >= authorized_from)
    if authorized_to is not None:
        stmt = stmt.where(ExternalCommunicationAuthorization.authorized_at <= authorized_to)

    stmt = stmt.order_by(
        ExternalCommunicationAuthorization.authorized_at.desc(),
        ExternalCommunicationAuthorization.id.desc(),
    ).limit(limit)

    records = list(db.session.scalars(stmt))
    return jsonify(
        {
            "items": [
                {
                    "id": row.id,
                    "organization_id": row.organization_id,
                    "user_id": row.user_id,
                    "username": row.username,
                    "user_role": row.user_role,
                    "channel": row.channel,
                    "communication_type": row.communication_type,
                    "campaign_id": row.campaign_id,
                    "batch_id": row.batch_id,
                    "warning_acknowledged": bool(row.warning_acknowledged),
                    "confirmation_phrase": row.confirmation_phrase,
                    "reviewer_name": row.reviewer_name,
                    "reviewer_role": row.reviewer_role,
                    "details": row.details_json or {},
                    "authorized_at": row.authorized_at.isoformat() if row.authorized_at else None,
                }
                for row in records
            ],
            "count": len(records),
        }
    )


@admin_bp.delete("/users/<int:user_id>")
@login_required
@roles_required("admin")
@require_step_up_auth
def remove_org_user_route(user_id: int):
    """Remove a user from the org (sets organization_id to null and deactivates).

    Does NOT delete the user record to preserve audit trail.
    """
    from ngo_homesuite.models.core import User, db

    if user_id == current_user.id:
        return jsonify({"error": "Cannot remove yourself"}), 400

    stmt = select(User).where(User.id == user_id, User.organization_id == _org_id()).limit(1)
    user = db.session.scalars(stmt).first()
    if user is None:
        return jsonify({"error": "not found"}), 404

    user.is_active = False
    user.organization_id = None
    db.session.commit()
    return ("", 204)


# ---------------------------------------------------------------------------
# Org settings
# ---------------------------------------------------------------------------

@admin_bp.get("/org")
@login_required
@roles_required("admin")
def get_org_route():
    from ngo_homesuite.models.core import Organization, db

    org = db.session.get(Organization, _org_id())
    if org is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": org.id,
        "name": org.name,
        "email": getattr(org, "email", None),
        "phone": getattr(org, "phone", None),
        "website": getattr(org, "website", None),
        "country": getattr(org, "country", None),
        "currency": getattr(org, "currency", "USD"),
        "is_active": getattr(org, "is_active", True),
    })


@admin_bp.patch("/org")
@login_required
@roles_required("admin")
@require_step_up_auth
def update_org_route():
    """Update org-level settings."""
    from ngo_homesuite.models.core import Organization, db

    data = request.get_json(silent=True) or {}
    org = db.session.get(Organization, _org_id())
    if org is None:
        return jsonify({"error": "not found"}), 404

    allowed = {"name", "email", "phone", "website", "country", "currency"}
    for key, value in data.items():
        if key in allowed and hasattr(org, key):
            setattr(org, key, value)
    db.session.commit()
    return jsonify({"id": org.id, "name": org.name})


@admin_bp.get("/custom-fields/schema")
@login_required
@roles_required("admin")
def get_custom_fields_schema_route():
    from ngo_homesuite.models.core import Organization, db

    org = db.session.get(Organization, _org_id())
    if org is None:
        return jsonify({"error": "not found"}), 404

    return jsonify({"schema": _get_org_custom_field_schema(org)})


@admin_bp.put("/custom-fields/schema")
@login_required
@roles_required("admin")
@require_step_up_auth
def put_custom_fields_schema_route():
    from ngo_homesuite.models.core import Organization, db

    org = db.session.get(Organization, _org_id())
    if org is None:
        return jsonify({"error": "not found"}), 404

    payload = request.get_json(silent=True) or {}
    candidate_schema = payload.get("schema", payload)
    schema, error = _validate_custom_field_schema(candidate_schema)
    if error:
        return jsonify({"error": error}), 400

    metadata = org.metadata_json if isinstance(org.metadata_json, dict) else {}
    metadata["custom_fields_schema"] = schema
    org.metadata_json = metadata
    db.session.commit()
    return jsonify({"schema": schema})


# ---------------------------------------------------------------------------
# Role summary (readonly)
# ---------------------------------------------------------------------------

@admin_bp.get("/roles")
@login_required
@roles_required("admin")
def list_roles_route():
    """Return role definitions and user counts per role in this org."""
    from ngo_homesuite.models.core import User, db

    rows = db.session.connection().exec_driver_sql(
        str(select(User.role, func.count(User.id)).where(User.organization_id == _org_id(), User.is_active == True).group_by(User.role).compile(compile_kwargs={"literal_binds": True}))
    ).all()
    counts = {role: count for role, count in rows}
    return jsonify({
        "roles": [
            {"role": "admin", "count": counts.get("admin", 0), "description": "Full access — can manage users and org settings"},
            {"role": "staff", "count": counts.get("staff", 0), "description": "Can create/edit cases, beneficiaries, donations, reports"},
            {"role": "volunteer", "count": counts.get("volunteer", 0), "description": "Read access to assigned programs; can log hours"},
            {"role": "viewer", "count": counts.get("viewer", 0), "description": "Read-only access to non-PII data"},
        ]
    })


# ---------------------------------------------------------------------------
# Compliance checks
# ---------------------------------------------------------------------------

@admin_bp.get("/compliance/audit")
@login_required
@roles_required("admin")
def compliance_audit_route():
    """Run full compliance audit for organization."""
    from ngo_homesuite.compliance.monitoring import ComplianceMonitoringService
    
    results = ComplianceMonitoringService.run_full_compliance_audit(_org_id())
    return jsonify(results)


@admin_bp.get("/compliance/grant-deadlines")
@login_required
@roles_required("admin")
def compliance_grant_deadlines_route():
    """Check grant deadlines and alert on overdue/approaching."""
    from ngo_homesuite.compliance.monitoring import ComplianceMonitoringService
    
    alerts = ComplianceMonitoringService.check_grant_deadlines(_org_id())
    return jsonify(alerts)


@admin_bp.get("/compliance/drift")
@login_required
@roles_required("admin")
def compliance_drift_route():
    """Detect compliance drift indicators."""
    from ngo_homesuite.compliance.monitoring import ComplianceMonitoringService
    
    drift = ComplianceMonitoringService.detect_compliance_drift(_org_id())
    return jsonify(drift)


@admin_bp.get("/compliance/grant/<int:grant_id>/readiness")
@login_required
@roles_required("admin")
def grant_readiness_route(grant_id: int):
    """Check grant pre-submission readiness."""
    from ngo_homesuite.models.core import Grant, db
    from ngo_homesuite.compliance.grant_validator import GrantPreSubmissionValidator
    
    grant = db.session.get(Grant, grant_id)
    if not grant or grant.organization_id != _org_id():
        return jsonify({"error": "not found"}), 404
    
    readiness = GrantPreSubmissionValidator.get_readiness_score(grant)
    return jsonify(readiness)


@admin_bp.get("/grants/<int:grant_id>/budget-summary")
@login_required
@roles_required("admin", "staff")
def grant_budget_summary_route(grant_id: int):
    """Per-grant budget utilisation summary for funder reporting."""
    from ngo_homesuite.grants.services import lifecycle as grant_svc
    from ngo_homesuite.grants.exceptions import GrantNotFound
    try:
        summary = grant_svc.get_grant_budget_summary(grant_id, _org_id())
    except GrantNotFound:
        return jsonify({"error": "Grant not found"}), 404
    return jsonify(summary), 200


@admin_bp.get("/grants/budget-workbench")
@login_required
@roles_required("admin", "staff")
def grant_budget_workbench_page():
    """Render the grant budget workbench UI."""
    return render_template(
        "admin/grant_budget_workbench.html",
        active_page="grant_budget_workbench",
    )


@admin_bp.get("/compliance/p2p/<int:page_id>/fraud-score")
@login_required
@roles_required("admin")
def p2p_fraud_score_route(page_id: int):
    """Get fraud risk score for P2P page."""
    from ngo_homesuite.models.core import P2PPage, db
    from ngo_homesuite.compliance.p2p_fraud_detector import P2PFraudDetector
    
    page = db.session.get(P2PPage, page_id)
    if not page or page.organization_id != _org_id():
        return jsonify({"error": "not found"}), 404
    
    fraud_score = P2PFraudDetector.get_fraud_score(page)
    return jsonify(fraud_score)


# ---------------------------------------------------------------------------
# TONY Grant Scoring
# ---------------------------------------------------------------------------

@admin_bp.post("/tony/score-grant/<int:grant_id>")
@login_required
@roles_required("admin")
def tony_score_grant_route(grant_id: int):
    """Score a grant using TONY algorithm.
    
    Query params:
    - preset: 'conservative', 'balanced', or 'lenient' (default: balanced)
    """
    from ngo_homesuite.models.core import Grant, GrantScore, db
    from ngo_homesuite.compliance.tony_scoring import TonyScorer
    
    preset = request.args.get("preset", "balanced").lower()
    if preset not in ("conservative", "balanced", "lenient"):
        return jsonify({"error": "preset must be conservative, balanced, or lenient"}), 400
    
    grant = db.session.get(Grant, grant_id)
    if not grant or grant.organization_id != _org_id():
        return jsonify({"error": "not found"}), 404
    
    try:
        score_result = TonyScorer.score_grant(grant_id, _org_id(), preset)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Scoring failed: {str(e)}"}), 500
    
    # Store score in database
    existing_score = (
        db.session.query(GrantScore)
        .filter(
            GrantScore.grant_id == grant_id,
            GrantScore.preset == preset,
        )
        .first()
    )
    
    if existing_score:
        # Update existing score
        existing_score.base_risk_probability = score_result["base_risk_probability"]
        existing_score.final_risk_probability = score_result["final_risk_probability"]
        existing_score.risk_descriptor = score_result["risk_descriptor"]
        existing_score.grant_recommendation_label = score_result["grant_recommendation"]["label"]
        existing_score.grant_recommendation_text = score_result["grant_recommendation"]["recommendation"]
        existing_score.altman_zscore = score_result["altman_zscore"]
        existing_score.altman_zone = score_result["altman_zone"]
        existing_score.organizational_health_score = score_result["organizational_health"]
        existing_score.features = score_result["features"]
        existing_score.financial_snapshot = score_result["financial_snapshot"]
        existing_score.risk_factors = score_result["grant_recommendation"]["risk_factors"]
        db.session.commit()
    else:
        # Create new score
        score_obj = GrantScore(
            grant_id=grant_id,
            organization_id=_org_id(),
            preset=preset,
            base_risk_probability=score_result["base_risk_probability"],
            final_risk_probability=score_result["final_risk_probability"],
            risk_descriptor=score_result["risk_descriptor"],
            grant_recommendation_label=score_result["grant_recommendation"]["label"],
            grant_recommendation_text=score_result["grant_recommendation"]["recommendation"],
            altman_zscore=score_result["altman_zscore"],
            altman_zone=score_result["altman_zone"],
            organizational_health_score=score_result["organizational_health"],
            features=score_result["features"],
            financial_snapshot=score_result["financial_snapshot"],
            risk_factors=score_result["grant_recommendation"]["risk_factors"],
        )
        db.session.add(score_obj)
        db.session.commit()
    
    return jsonify(score_result)


@admin_bp.get("/tony/score/<int:grant_id>")
@login_required
@roles_required("admin")
def tony_get_score_route(grant_id: int):
    """Get latest TONY score for a grant."""
    from ngo_homesuite.models.core import Grant, GrantScore, db
    
    grant = db.session.get(Grant, grant_id)
    if not grant or grant.organization_id != _org_id():
        return jsonify({"error": "not found"}), 404
    
    # Get latest score (any preset)
    latest_score = (
        db.session.query(GrantScore)
        .filter(GrantScore.grant_id == grant_id)
        .order_by(GrantScore.scored_at.desc())
        .first()
    )
    
    if not latest_score:
        return jsonify({"error": "no scores found"}), 404
    
    return jsonify({
        "id": latest_score.id,
        "grant_id": latest_score.grant_id,
        "preset": latest_score.preset,
        "scored_at": latest_score.scored_at.isoformat(),
        "base_risk_probability": latest_score.base_risk_probability,
        "final_risk_probability": latest_score.final_risk_probability,
        "risk_descriptor": latest_score.risk_descriptor,
        "grant_recommendation_label": latest_score.grant_recommendation_label,
        "grant_recommendation_text": latest_score.grant_recommendation_text,
        "altman_zscore": latest_score.altman_zscore,
        "altman_zone": latest_score.altman_zone,
        "organizational_health_score": latest_score.organizational_health_score,
        "features": latest_score.features,
        "financial_snapshot": latest_score.financial_snapshot,
        "risk_factors": latest_score.risk_factors,
    })


@admin_bp.get("/tony/audit")
@login_required
@roles_required("admin")
def tony_organization_audit_route():
    """Run full TONY audit for all organization grants.
    
    Query params:
    - preset: scoring preset (default: balanced)
    - limit: max grants to score (default: no limit)
    """
    from ngo_homesuite.compliance.tony_scoring import TonyScoringService
    
    preset = request.args.get("preset", "balanced").lower()
    limit = request.args.get("limit", type=int)
    
    try:
        audit = TonyScoringService.run_organization_audit(_org_id(), preset)
        return jsonify(audit)
    except Exception as e:
        return jsonify({"error": f"Audit failed: {str(e)}"}), 500


@admin_bp.get("/tony/recommendations")
@login_required
@roles_required("admin")
def tony_recommendations_route():
    """Get grants requiring action based on latest TONY scores.
    
    Returns grants with risk level >= Conditional.
    """
    from ngo_homesuite.models.core import Grant, GrantScore, db
    
    # Get grants with conditional or elevated risk (latest score per grant)
    subquery = (
        select(GrantScore.grant_id, func.max(GrantScore.scored_at))
        .where(GrantScore.organization_id == _org_id())
        .group_by(GrantScore.grant_id)
        .subquery()
    )
    
    risky_scores = (
        db.session.query(GrantScore)
        .join(subquery, (GrantScore.grant_id == subquery.c.grant_id) & (GrantScore.scored_at == subquery.c.max_1))
        .filter(GrantScore.grant_recommendation_label.in_(["Conditional", "Elevated Risk"]))
        .order_by(GrantScore.final_risk_probability.desc())
        .all()
    )
    
    return jsonify([
        {
            "grant_id": s.grant_id,
            "grant_name": s.grant.name if s.grant else "Unknown",
            "risk_level": s.grant_recommendation_label,
            "risk_probability": s.final_risk_probability,
            "recommendation": s.grant_recommendation_text,
            "risk_factors": s.risk_factors,
            "scored_at": s.scored_at.isoformat(),
        }
        for s in risky_scores
    ])
