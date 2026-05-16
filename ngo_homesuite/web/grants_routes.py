from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.web.rbac import roles_required


grants_bp = Blueprint("grants", __name__, url_prefix="/grants")


def _org_id() -> int:
    return int(current_user.organization_id)


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat((value or "").strip())


@grants_bp.get("/")
@login_required
def list_grants_route():
    from ngo_homesuite.services.grant_service import list_grants

    status = request.args.get("status")
    grants = list_grants(_org_id(), status=status)
    return jsonify([
        {
            "id": g.id,
            "title": g.title,
            "funder_name": g.funder_name,
            "status": g.status,
            "amount_requested": g.amount_requested,
            "amount_awarded": g.amount_awarded,
            "application_deadline": str(g.application_deadline) if g.application_deadline else None,
        }
        for g in grants
    ])


@grants_bp.post("/")
@login_required
@roles_required("admin", "staff")
def create_grant_route():
    from ngo_homesuite.services.grant_service import create_grant

    data = request.get_json(silent=True) or {}
    if not data.get("title") or not data.get("funder_name"):
        return jsonify({"error": "title and funder_name are required"}), 400

    try:
        grant = create_grant(_org_id(), **data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": grant.id, "title": grant.title, "status": grant.status}), 201


@grants_bp.post("/<int:grant_id>/advance")
@login_required
@roles_required("admin", "staff")
def advance_grant_route(grant_id: int):
    from ngo_homesuite.services.grant_service import advance_grant_status, GrantNotFound, InvalidGrantTransition

    data = request.get_json(silent=True) or {}
    new_status = data.get("new_status")
    if not new_status:
        return jsonify({"error": "new_status is required"}), 400

    try:
        grant = advance_grant_status(grant_id, _org_id(), new_status=new_status, **{k: v for k, v in data.items() if k != "new_status"})
    except GrantNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except InvalidGrantTransition as exc:
        return jsonify({"error": str(exc)}), 422
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": grant.id, "status": grant.status})


@grants_bp.post("/<int:grant_id>/disburse")
@login_required
@roles_required("admin", "staff")
def disburse_grant_route(grant_id: int):
    from ngo_homesuite.services.grant_service import add_disbursement, GrantNotFound

    data = request.get_json(silent=True) or {}
    if data.get("amount") is None or data.get("received_date") is None:
        return jsonify({"error": "amount and received_date are required"}), 400

    payload = dict(data)
    try:
        payload["received_date"] = _parse_iso_date(str(payload["received_date"]))
    except ValueError:
        return jsonify({"error": "received_date must be ISO format YYYY-MM-DD"}), 400

    try:
        disb = add_disbursement(grant_id, _org_id(), **payload)
    except GrantNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": disb.id, "amount": disb.amount}), 201


@grants_bp.get("/pipeline")
@login_required
def pipeline_route():
    from ngo_homesuite.services.grant_service import grant_pipeline_summary

    return jsonify(grant_pipeline_summary(_org_id()))
