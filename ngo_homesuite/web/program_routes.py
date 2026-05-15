from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.web.rbac import roles_required


program_bp = Blueprint("program", __name__, url_prefix="/programs")


def _org_id() -> int:
    return int(current_user.organization_id)


@program_bp.get("/cases")
@login_required
def list_cases_route():
    from ngo_homesuite.services.program_impact_service import list_cases

    cases = list_cases(
        _org_id(),
        status=request.args.get("status"),
        case_type=request.args.get("case_type"),
        donor_id=request.args.get("donor_id", type=int),
        project_id=request.args.get("project_id", type=int),
    )
    return jsonify([
        {
            "id": c.id,
            "title": c.title,
            "status": c.status,
            "case_type": c.case_type,
            "outcome_metric": c.outcome_metric,
            "outcome_value": c.outcome_value,
        }
        for c in cases
    ])


@program_bp.post("/cases")
@login_required
@roles_required("admin", "staff")
def create_case_route():
    from ngo_homesuite.services.program_impact_service import create_case

    data = request.get_json(silent=True) or {}
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400

    case = create_case(_org_id(), **data)
    return jsonify({"id": case.id, "status": case.status}), 201


@program_bp.get("/cases/<int:case_id>")
@login_required
def get_case_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import get_case

    case = get_case(case_id, _org_id())
    if case is None:
        return jsonify({"error": "not found"}), 404

    return jsonify(
        {
            "id": case.id,
            "title": case.title,
            "status": case.status,
            "case_type": case.case_type,
            "outcome_metric": case.outcome_metric,
            "outcome_value": case.outcome_value,
        }
    )


@program_bp.post("/cases/<int:case_id>/status")
@login_required
@roles_required("admin", "staff")
def update_status_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import update_case_status

    data = request.get_json(silent=True) or {}
    if not data.get("new_status"):
        return jsonify({"error": "new_status is required"}), 400

    case = update_case_status(case_id, _org_id(), **data)
    return jsonify({"id": case.id, "status": case.status})


@program_bp.post("/cases/<int:case_id>/notes")
@login_required
@roles_required("admin", "staff")
def add_note_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import add_note

    data = request.get_json(silent=True) or {}
    if not data.get("description"):
        return jsonify({"error": "description is required"}), 400

    activity = add_note(case_id, _org_id(), data["description"])
    return jsonify({"id": activity.id, "activity_type": activity.activity_type}), 201


@program_bp.get("/impact")
@login_required
def impact_route():
    from ngo_homesuite.services.program_impact_service import impact_report

    case_type = request.args.get("case_type")
    return jsonify(impact_report(_org_id(), case_type=case_type))
