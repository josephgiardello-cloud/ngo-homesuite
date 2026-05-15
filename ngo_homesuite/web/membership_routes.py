from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.web.rbac import roles_required


membership_bp = Blueprint("membership", __name__, url_prefix="/membership")


def _org_id() -> int:
    return int(current_user.organization_id)


@membership_bp.get("/tiers")
@login_required
def list_tiers_route():
    from ngo_homesuite.services.membership_service import list_tiers

    tiers = list_tiers(_org_id())
    return jsonify([
        {
            "id": t.id,
            "name": t.name,
            "price": t.price,
            "interval": t.interval,
            "is_active": bool(t.is_active),
        }
        for t in tiers
    ])


@membership_bp.post("/tiers")
@login_required
@roles_required("admin", "staff")
def create_tier_route():
    from ngo_homesuite.services.membership_service import create_tier

    data = request.get_json(silent=True) or {}
    if not data.get("name") or data.get("price") is None:
        return jsonify({"error": "name and price are required"}), 400

    tier = create_tier(_org_id(), **data)
    return jsonify({"id": tier.id, "name": tier.name}), 201


@membership_bp.post("/members")
@login_required
@roles_required("admin", "staff")
def enroll_member_route():
    from ngo_homesuite.services.membership_service import enroll_member

    data = request.get_json(silent=True) or {}
    if data.get("donor_id") is None or data.get("tier_id") is None:
        return jsonify({"error": "donor_id and tier_id are required"}), 400

    rec = enroll_member(_org_id(), **data)
    return jsonify({"id": rec.id, "status": rec.status}), 201


@membership_bp.post("/members/<int:record_id>/renew")
@login_required
@roles_required("admin", "staff")
def renew_member_route(record_id: int):
    from ngo_homesuite.services.membership_service import renew_membership

    rec = renew_membership(record_id, _org_id())
    return jsonify({"id": rec.id, "status": rec.status})


@membership_bp.post("/members/<int:record_id>/cancel")
@login_required
@roles_required("admin", "staff")
def cancel_member_route(record_id: int):
    from ngo_homesuite.services.membership_service import cancel_membership

    rec = cancel_membership(record_id, _org_id())
    return jsonify({"id": rec.id, "status": rec.status})


@membership_bp.get("/summary")
@login_required
def summary_route():
    from ngo_homesuite.services.membership_service import membership_summary

    return jsonify(membership_summary(_org_id()))
