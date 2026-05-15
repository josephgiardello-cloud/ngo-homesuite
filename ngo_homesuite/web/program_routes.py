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
            "intake_stage": c.intake_stage,
            "risk_level": c.risk_level,
            "progress_percent": c.progress_percent,
            "outcome_metric": c.outcome_metric,
            "outcome_value": c.outcome_value,
            "target_outcome_value": c.target_outcome_value,
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
            "intake_stage": case.intake_stage,
            "risk_level": case.risk_level,
            "progress_percent": case.progress_percent,
            "outcome_metric": case.outcome_metric,
            "outcome_value": case.outcome_value,
            "target_outcome_value": case.target_outcome_value,
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


@program_bp.put("/intake/beneficiaries/<int:beneficiary_id>")
@login_required
@roles_required("admin", "staff")
def update_intake_route(beneficiary_id: int):
    from ngo_homesuite.services.program_impact_service import update_beneficiary_intake

    data = request.get_json(silent=True) or {}
    beneficiary = update_beneficiary_intake(beneficiary_id, _org_id(), **data)
    return jsonify(
        {
            "id": beneficiary.id,
            "first_name": beneficiary.first_name,
            "last_name": beneficiary.last_name,
            "program": beneficiary.program,
            "status": beneficiary.status,
        }
    )


@program_bp.post("/cases/<int:case_id>/service-logs")
@login_required
@roles_required("admin", "staff")
def add_service_log_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import log_service_delivery

    data = request.get_json(silent=True) or {}
    if not data.get("service_type"):
        return jsonify({"error": "service_type is required"}), 400

    log = log_service_delivery(case_id, _org_id(), **data)
    return jsonify({"id": log.id, "service_type": log.service_type}), 201


@program_bp.get("/cases/<int:case_id>/service-logs")
@login_required
def list_service_logs_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import list_service_logs

    logs = list_service_logs(case_id, _org_id())
    return jsonify(
        [
            {
                "id": item.id,
                "service_type": item.service_type,
                "service_date": item.service_date.isoformat(),
                "duration_minutes": item.duration_minutes,
                "service_units": item.service_units,
                "outcome_note": item.outcome_note,
            }
            for item in logs
        ]
    )


@program_bp.post("/cases/<int:case_id>/outcomes")
@login_required
@roles_required("admin", "staff")
def add_outcome_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import record_outcome_metric

    data = request.get_json(silent=True) or {}
    if not data.get("metric_name"):
        return jsonify({"error": "metric_name is required"}), 400
    if data.get("current_value") is None:
        return jsonify({"error": "current_value is required"}), 400

    metric = record_outcome_metric(case_id, _org_id(), **data)
    return jsonify({"id": metric.id, "metric_name": metric.metric_name, "current_value": metric.current_value}), 201


@program_bp.get("/cases/<int:case_id>/progress")
@login_required
def case_progress_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import case_progress

    return jsonify(case_progress(case_id, _org_id()))
