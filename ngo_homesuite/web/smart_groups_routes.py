from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.web.rbac import roles_required


smart_groups_bp = Blueprint("smart_groups", __name__, url_prefix="/smart-groups")


def _org_id() -> int:
    return int(current_user.organization_id)


@smart_groups_bp.get("/")
@login_required
def list_groups_route():
    from ngo_homesuite.services.smart_groups_service import list_groups

    groups = list_groups(_org_id())
    return jsonify([
        {
            "id": g.id,
            "name": g.name,
            "last_count": g.last_count,
            "last_evaluated_at": g.last_evaluated_at.isoformat() if g.last_evaluated_at else None,
        }
        for g in groups
    ])


@smart_groups_bp.post("/")
@login_required
@roles_required("admin", "staff")
def create_group_route():
    from ngo_homesuite.services.smart_groups_service import create_group

    data = request.get_json(silent=True) or {}
    if not data.get("name") or data.get("rules") is None:
        return jsonify({"error": "name and rules are required"}), 400

    group = create_group(_org_id(), **data)
    return jsonify({"id": group.id, "name": group.name}), 201


@smart_groups_bp.get("/<int:group_id>/evaluate")
@login_required
def evaluate_group_route(group_id: int):
    from ngo_homesuite.services.smart_groups_service import evaluate_group

    members = evaluate_group(group_id, _org_id())
    return jsonify({"count": len(members), "members": members[:200]})


@smart_groups_bp.post("/evaluate-all")
@login_required
@roles_required("admin", "staff")
def evaluate_all_route():
    from ngo_homesuite.services.smart_groups_service import evaluate_all_groups

    result = evaluate_all_groups(_org_id())
    return jsonify(result)
