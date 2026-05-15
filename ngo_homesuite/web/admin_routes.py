"""Admin routes: org user management, role assignment, org settings.

All endpoints require the 'admin' role.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.web.rbac import roles_required


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _org_id() -> int:
    return int(current_user.organization_id)


# ---------------------------------------------------------------------------
# User management within org
# ---------------------------------------------------------------------------

@admin_bp.get("/users")
@login_required
@roles_required("admin")
def list_org_users_route():
    """List all users in the current org."""
    from ngo_homesuite.models.core import User

    users = User.query.filter_by(organization_id=_org_id()).order_by(User.created_at.asc()).all()
    return jsonify([
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in users
    ])


@admin_bp.get("/users/<int:user_id>")
@login_required
@roles_required("admin")
def get_org_user_route(user_id: int):
    from ngo_homesuite.models.core import User

    user = User.query.filter_by(id=user_id, organization_id=_org_id()).first()
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
        "phone": user.phone,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    })


ALLOWED_ROLES = {"admin", "staff", "volunteer", "viewer"}


@admin_bp.patch("/users/<int:user_id>/role")
@login_required
@roles_required("admin")
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

    user = User.query.filter_by(id=user_id, organization_id=_org_id()).first()
    if user is None:
        return jsonify({"error": "not found"}), 404

    user.role = new_role
    db.session.commit()
    return jsonify({"id": user.id, "username": user.username, "role": user.role})


@admin_bp.patch("/users/<int:user_id>/status")
@login_required
@roles_required("admin")
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

    user = User.query.filter_by(id=user_id, organization_id=_org_id()).first()
    if user is None:
        return jsonify({"error": "not found"}), 404

    user.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify({"id": user.id, "is_active": user.is_active})


@admin_bp.delete("/users/<int:user_id>")
@login_required
@roles_required("admin")
def remove_org_user_route(user_id: int):
    """Remove a user from the org (sets organization_id to null and deactivates).

    Does NOT delete the user record to preserve audit trail.
    """
    from ngo_homesuite.models.core import User, db

    if user_id == current_user.id:
        return jsonify({"error": "Cannot remove yourself"}), 400

    user = User.query.filter_by(id=user_id, organization_id=_org_id()).first()
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
    from ngo_homesuite.models.core import Organization

    org = Organization.query.get(_org_id())
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
def update_org_route():
    """Update org-level settings."""
    from ngo_homesuite.models.core import Organization, db

    data = request.get_json(silent=True) or {}
    org = Organization.query.get(_org_id())
    if org is None:
        return jsonify({"error": "not found"}), 404

    allowed = {"name", "email", "phone", "website", "country", "currency"}
    for key, value in data.items():
        if key in allowed and hasattr(org, key):
            setattr(org, key, value)
    db.session.commit()
    return jsonify({"id": org.id, "name": org.name})


# ---------------------------------------------------------------------------
# Role summary (readonly)
# ---------------------------------------------------------------------------

@admin_bp.get("/roles")
@login_required
@roles_required("admin")
def list_roles_route():
    """Return role definitions and user counts per role in this org."""
    from ngo_homesuite.models.core import User
    from sqlalchemy import func
    from ngo_homesuite.models.core import db

    rows = (
        db.session.query(User.role, func.count(User.id))
        .filter(User.organization_id == _org_id(), User.is_active == True)
        .group_by(User.role)
        .all()
    )
    counts = {role: count for role, count in rows}
    return jsonify({
        "roles": [
            {"role": "admin", "count": counts.get("admin", 0), "description": "Full access — can manage users and org settings"},
            {"role": "staff", "count": counts.get("staff", 0), "description": "Can create/edit cases, beneficiaries, donations, reports"},
            {"role": "volunteer", "count": counts.get("volunteer", 0), "description": "Read access to assigned programs; can log hours"},
            {"role": "viewer", "count": counts.get("viewer", 0), "description": "Read-only access to non-PII data"},
        ]
    })
