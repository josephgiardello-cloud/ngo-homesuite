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


@program_bp.patch("/cases/<int:case_id>")
@login_required
@roles_required("admin", "staff")
def update_case_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import update_case_details

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "no fields provided"}), 400

    case = update_case_details(case_id, _org_id(), **data)
    return jsonify(
        {
            "id": case.id,
            "title": case.title,
            "status": case.status,
            "case_type": case.case_type,
            "intake_stage": case.intake_stage,
            "risk_level": case.risk_level,
            "progress_percent": case.progress_percent,
            "target_outcome_value": case.target_outcome_value,
        }
    )


@program_bp.delete("/cases/<int:case_id>")
@login_required
@roles_required("admin", "staff")
def delete_case_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import delete_case

    delete_case(case_id, _org_id())
    return ("", 204)


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


@program_bp.post("/intake/beneficiaries")
@login_required
@roles_required("admin", "staff")
def create_intake_beneficiary_route():
    from ngo_homesuite.services.beneficiary_service import create_beneficiary

    data = request.get_json(silent=True) or {}
    if not data.get("first_name") or not data.get("last_name"):
        return jsonify({"error": "first_name and last_name are required"}), 400

    beneficiary = create_beneficiary(_org_id(), **data)
    return jsonify(
        {
            "id": beneficiary.id,
            "first_name": beneficiary.first_name,
            "last_name": beneficiary.last_name,
            "program": beneficiary.program,
            "status": beneficiary.status,
        }
    ), 201


@program_bp.get("/intake/beneficiaries")
@login_required
def list_intake_beneficiaries_route():
    from ngo_homesuite.services.beneficiary_service import list_beneficiaries

    beneficiaries = list_beneficiaries(
        _org_id(),
        program=request.args.get("program"),
        status=request.args.get("status"),
    )
    return jsonify(
        [
            {
                "id": b.id,
                "first_name": b.first_name,
                "last_name": b.last_name,
                "email": b.email,
                "phone": b.phone,
                "city": b.city,
                "program": b.program,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in beneficiaries
        ]
    )


@program_bp.get("/intake/beneficiaries/<int:beneficiary_id>")
@login_required
def get_intake_beneficiary_route(beneficiary_id: int):
    from ngo_homesuite.services.beneficiary_service import get_beneficiary

    beneficiary = get_beneficiary(beneficiary_id, _org_id())
    if beneficiary is None:
        return jsonify({"error": "not found"}), 404

    return jsonify(
        {
            "id": beneficiary.id,
            "first_name": beneficiary.first_name,
            "last_name": beneficiary.last_name,
            "email": beneficiary.email,
            "phone": beneficiary.phone,
            "country": beneficiary.country,
            "city": beneficiary.city,
            "address": beneficiary.address,
            "program": beneficiary.program,
            "status": beneficiary.status,
            "notes": beneficiary.notes,
        }
    )


@program_bp.get("/intake/beneficiaries/<int:beneficiary_id>/profile")
@login_required
def beneficiary_profile_route(beneficiary_id: int):
    from ngo_homesuite.services.program_impact_service import beneficiary_profile

    payload = beneficiary_profile(beneficiary_id, _org_id())
    return jsonify(payload)


@program_bp.get("/intake/beneficiaries/<int:beneficiary_id>/timeline")
@login_required
def beneficiary_timeline_route(beneficiary_id: int):
    from ngo_homesuite.services.program_impact_service import beneficiary_timeline

    limit = request.args.get("limit", 100, type=int)
    payload = beneficiary_timeline(beneficiary_id, _org_id(), limit=limit)
    return jsonify(payload)


@program_bp.delete("/intake/beneficiaries/<int:beneficiary_id>")
@login_required
@roles_required("admin", "staff")
def delete_intake_beneficiary_route(beneficiary_id: int):
    from ngo_homesuite.services.beneficiary_service import delete_beneficiary

    delete_beneficiary(beneficiary_id, _org_id())
    return ("", 204)


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


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------

@program_bp.post("/cases/<int:case_id>/assessments")
@login_required
@roles_required("admin", "staff")
def create_assessment_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import create_assessment

    data = request.get_json(silent=True) or {}
    if not data.get("assessment_date"):
        return jsonify({"error": "assessment_date is required"}), 400

    assessment = create_assessment(
        case_id, _org_id(), data.pop("assessment_date"), **data
    )
    return jsonify({
        "id": assessment.id,
        "assessment_type": assessment.assessment_type,
        "risk_level": assessment.risk_level,
        "total_score": assessment.total_score,
        "assessment_date": assessment.assessment_date.isoformat(),
    }), 201


@program_bp.get("/cases/<int:case_id>/assessments")
@login_required
def list_assessments_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import list_assessments

    items = list_assessments(case_id, _org_id())
    return jsonify([
        {
            "id": a.id,
            "assessment_type": a.assessment_type,
            "assessment_date": a.assessment_date.isoformat(),
            "risk_level": a.risk_level,
            "total_score": a.total_score,
            "housing_score": a.housing_score,
            "food_security_score": a.food_security_score,
            "health_score": a.health_score,
            "employment_score": a.employment_score,
            "safety_score": a.safety_score,
            "education_score": a.education_score,
            "notes": a.notes,
        }
        for a in items
    ])


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------

@program_bp.post("/cases/<int:case_id>/referrals")
@login_required
@roles_required("admin", "staff")
def create_referral_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import create_referral

    data = request.get_json(silent=True) or {}
    if not data.get("provider_name"):
        return jsonify({"error": "provider_name is required"}), 400
    if not data.get("referral_date"):
        return jsonify({"error": "referral_date is required"}), 400

    referral = create_referral(case_id, _org_id(), data.pop("provider_name"), data.pop("referral_date"), **data)
    return jsonify({
        "id": referral.id,
        "provider_name": referral.provider_name,
        "service_type": referral.service_type,
        "status": referral.status,
        "referral_date": referral.referral_date.isoformat(),
    }), 201


@program_bp.get("/cases/<int:case_id>/referrals")
@login_required
def list_referrals_route(case_id: int):
    from ngo_homesuite.services.program_impact_service import list_referrals

    items = list_referrals(case_id, _org_id())
    return jsonify([
        {
            "id": r.id,
            "referral_type": r.referral_type,
            "provider_name": r.provider_name,
            "service_type": r.service_type,
            "referral_date": r.referral_date.isoformat(),
            "status": r.status,
            "outcome_date": r.outcome_date.isoformat() if r.outcome_date else None,
            "outcome_notes": r.outcome_notes,
        }
        for r in items
    ])


@program_bp.patch("/cases/<int:case_id>/referrals/<int:referral_id>")
@login_required
@roles_required("admin", "staff")
def update_referral_route(case_id: int, referral_id: int):
    from ngo_homesuite.services.program_impact_service import update_referral_status

    data = request.get_json(silent=True) or {}
    if not data.get("status"):
        return jsonify({"error": "status is required"}), 400

    referral = update_referral_status(
        referral_id, case_id, _org_id(),
        data["status"],
        outcome_date=data.get("outcome_date"),
        outcome_notes=data.get("outcome_notes"),
    )
    return jsonify({"id": referral.id, "status": referral.status})


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

@program_bp.post("/appointments")
@login_required
@roles_required("admin", "staff")
def create_appointment_route():
    from ngo_homesuite.services.program_impact_service import create_appointment

    data = request.get_json(silent=True) or {}
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400
    if not data.get("scheduled_at"):
        return jsonify({"error": "scheduled_at is required"}), 400

    appt = create_appointment(_org_id(), data.pop("title"), data.pop("scheduled_at"), **data)
    return jsonify({
        "id": appt.id,
        "title": appt.title,
        "appointment_type": appt.appointment_type,
        "scheduled_at": appt.scheduled_at.isoformat(),
        "status": appt.status,
    }), 201


@program_bp.get("/appointments")
@login_required
def list_appointments_route():
    from ngo_homesuite.services.program_impact_service import list_appointments

    appts = list_appointments(
        _org_id(),
        case_id=request.args.get("case_id", type=int),
        beneficiary_id=request.args.get("beneficiary_id", type=int),
        staff_id=request.args.get("staff_id", type=int),
        status=request.args.get("status"),
    )
    return jsonify([
        {
            "id": a.id,
            "title": a.title,
            "appointment_type": a.appointment_type,
            "scheduled_at": a.scheduled_at.isoformat(),
            "duration_minutes": a.duration_minutes,
            "location": a.location,
            "is_virtual": a.is_virtual,
            "status": a.status,
            "case_id": a.case_id,
            "beneficiary_id": a.beneficiary_id,
        }
        for a in appts
    ])


@program_bp.get("/appointments/<int:appointment_id>")
@login_required
def get_appointment_route(appointment_id: int):
    from ngo_homesuite.services.program_impact_service import get_appointment

    appt = get_appointment(appointment_id, _org_id())
    if appt is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": appt.id,
        "title": appt.title,
        "appointment_type": appt.appointment_type,
        "scheduled_at": appt.scheduled_at.isoformat(),
        "duration_minutes": appt.duration_minutes,
        "location": appt.location,
        "is_virtual": appt.is_virtual,
        "meeting_link": appt.meeting_link,
        "status": appt.status,
        "notes": appt.notes,
        "case_id": appt.case_id,
        "beneficiary_id": appt.beneficiary_id,
        "staff_id": appt.staff_id,
    })


@program_bp.patch("/appointments/<int:appointment_id>")
@login_required
@roles_required("admin", "staff")
def update_appointment_route(appointment_id: int):
    from ngo_homesuite.services.program_impact_service import update_appointment

    data = request.get_json(silent=True) or {}
    appt = update_appointment(appointment_id, _org_id(), **data)
    return jsonify({"id": appt.id, "status": appt.status, "scheduled_at": appt.scheduled_at.isoformat()})


@program_bp.delete("/appointments/<int:appointment_id>")
@login_required
@roles_required("admin", "staff")
def cancel_appointment_route(appointment_id: int):
    from ngo_homesuite.services.program_impact_service import cancel_appointment

    cancel_appointment(appointment_id, _org_id())
    return ("", 204)

