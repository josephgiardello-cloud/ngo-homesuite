"""Program Impact / Case Management service.

CiviCRM-style case tracking: each ProgramCase has an immutable activity log
and can track qualitative + quantitative outcomes.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from ngo_homesuite.models.core import (
    Beneficiary,
    BeneficiaryAppointment,
    BeneficiaryAssessment,
    BeneficiaryReferral,
    BeneficiaryServiceLog,
    CaseActivity,
    CaseOutcomeMetric,
    ProgramCase,
    db,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return _utcnow()


def _coerce_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return _utcnow().date()
        if "T" in cleaned:
            return _coerce_datetime(cleaned).date()
        return date.fromisoformat(cleaned)
    return _utcnow().date()


# ---------------------------------------------------------------------------
# Case CRUD
# ---------------------------------------------------------------------------

def create_case(
    organization_id: int,
    title: str,
    *,
    donor_id: Optional[int] = None,
    project_id: Optional[int] = None,
    case_type: str = "service",
    description: Optional[str] = None,
    outcome_metric: Optional[str] = None,
    target_outcome_value: Optional[float] = None,
    next_review_date: Optional[Any] = None,
    beneficiary_id: Optional[int] = None,
    intake_stage: str = "intake",
    risk_level: Optional[str] = None,
    intake_summary: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> ProgramCase:
    case = ProgramCase(
        organization_id=organization_id,
        title=title,
        donor_id=donor_id,
        project_id=project_id,
        beneficiary_id=beneficiary_id,
        case_type=case_type,
        description=description,
        outcome_metric=outcome_metric,
        target_outcome_value=target_outcome_value,
        intake_stage=intake_stage,
        risk_level=risk_level,
        intake_summary=intake_summary,
        next_review_date=_coerce_date(next_review_date) if next_review_date is not None else None,
        status="open",
    )
    db.session.add(case)
    db.session.flush()

    _log_activity(
        case.id,
        organization_id,
        activity_type="status_change",
        content="Case opened",
        previous_status=None,
        new_status="open",
        actor_id=actor_id,
    )
    db.session.commit()
    return case


def get_case(case_id: int, organization_id: int) -> Optional[ProgramCase]:
    return ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first()


def list_cases(
    organization_id: int,
    *,
    status: Optional[str] = None,
    case_type: Optional[str] = None,
    donor_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> List[ProgramCase]:
    q = ProgramCase.query.filter_by(organization_id=organization_id)
    if status:
        q = q.filter_by(status=status)
    if case_type:
        q = q.filter_by(case_type=case_type)
    if donor_id:
        q = q.filter_by(donor_id=donor_id)
    if project_id:
        q = q.filter_by(project_id=project_id)
    return q.order_by(ProgramCase.created_at.desc()).all()


def update_case_status(
    case_id: int,
    organization_id: int,
    new_status: str,
    *,
    outcome_value: Optional[float] = None,
    notes: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> ProgramCase:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    old_status = case.status
    case.status = new_status
    if outcome_value is not None:
        case.outcome_value = outcome_value
    if new_status == "closed":
        case.closed_date = _utcnow().date()

    _log_activity(
        case_id,
        organization_id,
        activity_type="status_change",
        content=notes or f"Status changed from {old_status} to {new_status}",
        previous_status=old_status,
        new_status=new_status,
        actor_id=actor_id,
    )
    db.session.commit()
    return case


def update_case_details(
    case_id: int,
    organization_id: int,
    **fields,
) -> ProgramCase:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    allowed = {
        "title",
        "case_type",
        "priority",
        "description",
        "outcome",
        "outcome_metric",
        "outcome_value",
        "target_outcome_value",
        "intake_stage",
        "risk_level",
        "intake_summary",
        "donor_id",
        "project_id",
        "grant_id",
        "beneficiary_id",
        "next_review_date",
        "opened_date",
        "closed_date",
    }
    changed: list[str] = []

    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in {"next_review_date", "opened_date", "closed_date"}:
            setattr(case, key, _coerce_date(value) if value is not None else None)
        else:
            setattr(case, key, value)
        changed.append(key)

    if "outcome_value" in changed or "target_outcome_value" in changed:
        _recompute_case_progress(case)

    if changed:
        _log_activity(
            case.id,
            organization_id,
            activity_type="case_update",
            content=f"Case fields updated: {', '.join(sorted(changed))}",
        )

    db.session.commit()
    return case


def add_note(
    case_id: int,
    organization_id: int,
    description: str,
    *,
    activity_type: str = "note",
    actor_id: Optional[int] = None,
) -> CaseActivity:
    ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    activity = _log_activity(
        case_id,
        organization_id,
        activity_type=activity_type,
        content=description,
        actor_id=actor_id,
    )
    db.session.commit()
    return activity


def update_beneficiary_intake(
    beneficiary_id: int,
    organization_id: int,
    *,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    program: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> Beneficiary:
    beneficiary = Beneficiary.query.filter_by(id=beneficiary_id, organization_id=organization_id).first_or_404()
    fields = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
        "country": country,
        "city": city,
        "address": address,
        "program": program,
        "status": status,
        "notes": notes,
    }
    for key, value in fields.items():
        if value is not None:
            setattr(beneficiary, key, value)
    db.session.commit()
    return beneficiary


def log_service_delivery(
    case_id: int,
    organization_id: int,
    *,
    service_type: str,
    service_date: Optional[Any] = None,
    duration_minutes: Optional[int] = None,
    service_units: Optional[float] = None,
    outcome_note: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    staff_user_id: Optional[int] = None,
) -> BeneficiaryServiceLog:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    log = BeneficiaryServiceLog(
        organization_id=organization_id,
        case_id=case.id,
        beneficiary_id=case.beneficiary_id,
        staff_user_id=staff_user_id,
        service_type=service_type,
        service_date=_coerce_datetime(service_date) if service_date is not None else _utcnow(),
        duration_minutes=duration_minutes,
        service_units=service_units,
        outcome_note=outcome_note,
        metadata_json=metadata,
    )
    db.session.add(log)
    _log_activity(
        case.id,
        organization_id,
        activity_type="service_log",
        content=f"Service logged: {service_type}",
        actor_id=staff_user_id,
    )
    db.session.commit()
    return log


def list_service_logs(case_id: int, organization_id: int) -> List[BeneficiaryServiceLog]:
    ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    return (
        BeneficiaryServiceLog.query
        .filter_by(case_id=case_id, organization_id=organization_id)
        .order_by(BeneficiaryServiceLog.service_date.asc())
        .all()
    )


def record_outcome_metric(
    case_id: int,
    organization_id: int,
    *,
    metric_name: str,
    current_value: float,
    unit: Optional[str] = None,
    baseline_value: Optional[float] = None,
    target_value: Optional[float] = None,
    note: Optional[str] = None,
) -> CaseOutcomeMetric:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    metric = CaseOutcomeMetric(
        organization_id=organization_id,
        case_id=case.id,
        metric_name=metric_name,
        current_value=current_value,
        unit=unit,
        baseline_value=baseline_value,
        target_value=target_value,
        note=note,
    )
    db.session.add(metric)

    if case.outcome_metric == metric_name or case.outcome_metric is None:
        case.outcome_metric = metric_name
        case.outcome_value = current_value
        if target_value is not None and case.target_outcome_value is None:
            case.target_outcome_value = target_value

    _recompute_case_progress(case)
    _log_activity(
        case.id,
        organization_id,
        activity_type="outcome_metric",
        content=f"Outcome metric updated: {metric_name}={current_value}",
    )
    db.session.commit()
    return metric


def case_progress(case_id: int, organization_id: int) -> Dict[str, Any]:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    logs = list_service_logs(case_id, organization_id)
    metrics = (
        CaseOutcomeMetric.query
        .filter_by(case_id=case_id, organization_id=organization_id)
        .order_by(CaseOutcomeMetric.recorded_at.asc())
        .all()
    )
    activities = (
        CaseActivity.query
        .filter_by(case_id=case_id, organization_id=organization_id)
        .order_by(CaseActivity.created_at.asc())
        .all()
    )

    return {
        "case_id": case.id,
        "status": case.status,
        "intake_stage": case.intake_stage,
        "progress_percent": round(float(case.progress_percent or 0.0), 2),
        "service_count": len(logs),
        "last_service_at": logs[-1].service_date.isoformat() if logs else None,
        "metrics": [
            {
                "metric_name": m.metric_name,
                "current_value": m.current_value,
                "target_value": m.target_value,
                "recorded_at": m.recorded_at.isoformat(),
            }
            for m in metrics
        ],
        "timeline": [
            {
                "activity_type": a.activity_type,
                "content": a.content,
                "created_at": a.created_at.isoformat(),
            }
            for a in activities
        ],
    }


def delete_case(case_id: int, organization_id: int) -> None:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    db.session.delete(case)
    db.session.commit()


def _recompute_case_progress(case: ProgramCase) -> None:
    if case.target_outcome_value and case.target_outcome_value > 0 and case.outcome_value is not None:
        pct = (float(case.outcome_value) / float(case.target_outcome_value)) * 100.0
        case.progress_percent = max(0.0, min(100.0, pct))
        return

    latest = (
        CaseOutcomeMetric.query
        .filter_by(case_id=case.id, organization_id=case.organization_id)
        .order_by(CaseOutcomeMetric.recorded_at.desc())
        .first()
    )
    if latest and latest.target_value and latest.target_value > 0:
        pct = (float(latest.current_value) / float(latest.target_value)) * 100.0
        case.progress_percent = max(0.0, min(100.0, pct))


# ---------------------------------------------------------------------------
# Internal activity logger
# ---------------------------------------------------------------------------

def _log_activity(
    case_id: int,
    organization_id: int,
    activity_type: str,
    content: str,
    *,
    previous_status: Optional[str] = None,
    new_status: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> CaseActivity:
    activity = CaseActivity(
        case_id=case_id,
        organization_id=organization_id,
        activity_type=activity_type,
        content=content,
        previous_status=previous_status,
        new_status=new_status,
        actor_id=actor_id,
    )
    db.session.add(activity)
    return activity


# ---------------------------------------------------------------------------
# Impact reporting
# ---------------------------------------------------------------------------

def impact_report(organization_id: int, *, case_type: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate outcome metrics across all cases, optionally filtered by case_type."""
    q = ProgramCase.query.filter_by(organization_id=organization_id)
    if case_type:
        q = q.filter_by(case_type=case_type)
    cases = q.all()

    by_status: Dict[str, int] = {}
    outcomes: List[Dict[str, Any]] = []
    for case in cases:
        by_status[case.status] = by_status.get(case.status, 0) + 1
        if case.outcome_metric and case.outcome_value is not None:
            outcomes.append(
                {
                    "case_id": case.id,
                    "title": case.title,
                    "metric": case.outcome_metric,
                    "value": case.outcome_value,
                }
            )

    return {
        "case_type": case_type or "all",
        "case_count": len(cases),
        "by_status": by_status,
        "outcomes": outcomes,
        "avg_outcome": (
            round(sum(o["value"] for o in outcomes) / len(outcomes), 2) if outcomes else None
        ),
    }


def list_case_types(organization_id: int) -> List[str]:
    """Return distinct case_type values used by this org."""
    rows = (
        db.session.query(ProgramCase.case_type)
        .filter(ProgramCase.organization_id == organization_id)
        .distinct()
        .all()
    )
    return sorted(r[0] for r in rows if r[0])


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------

def create_assessment(
    case_id: int,
    organization_id: int,
    assessment_date: Any,
    *,
    assessment_type: str = "initial",
    housing_score: Optional[float] = None,
    food_security_score: Optional[float] = None,
    health_score: Optional[float] = None,
    employment_score: Optional[float] = None,
    safety_score: Optional[float] = None,
    education_score: Optional[float] = None,
    extra_domains: Optional[Dict[str, Any]] = None,
    risk_level: str = "medium",
    notes: Optional[str] = None,
    assessor_id: Optional[int] = None,
) -> BeneficiaryAssessment:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()

    domain_scores = [s for s in [
        housing_score, food_security_score, health_score,
        employment_score, safety_score, education_score,
    ] if s is not None]
    total = round(sum(domain_scores) / len(domain_scores), 2) if domain_scores else None

    assessment = BeneficiaryAssessment(
        case_id=case.id,
        organization_id=organization_id,
        assessor_id=assessor_id,
        assessment_type=assessment_type,
        assessment_date=_coerce_date(assessment_date),
        housing_score=housing_score,
        food_security_score=food_security_score,
        health_score=health_score,
        employment_score=employment_score,
        safety_score=safety_score,
        education_score=education_score,
        total_score=total,
        risk_level=risk_level,
        extra_domains=extra_domains,
        notes=notes,
    )
    db.session.add(assessment)
    # Update case risk level to match latest assessment
    case.risk_level = risk_level
    db.session.commit()
    _log_activity(case.id, organization_id, "assessment_added", f"Assessment type={assessment_type} risk={risk_level}")
    db.session.commit()
    return assessment


def list_assessments(case_id: int, organization_id: int) -> List[BeneficiaryAssessment]:
    ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    return (
        BeneficiaryAssessment.query.filter_by(case_id=case_id)
        .order_by(BeneficiaryAssessment.assessment_date.desc())
        .all()
    )


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------

def create_referral(
    case_id: int,
    organization_id: int,
    provider_name: str,
    referral_date: Any,
    *,
    referral_type: str = "external",
    provider_contact: Optional[str] = None,
    provider_email: Optional[str] = None,
    provider_phone: Optional[str] = None,
    service_type: Optional[str] = None,
    notes: Optional[str] = None,
    referred_by_id: Optional[int] = None,
) -> BeneficiaryReferral:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    referral = BeneficiaryReferral(
        case_id=case.id,
        organization_id=organization_id,
        referred_by_id=referred_by_id,
        referral_type=referral_type,
        provider_name=provider_name,
        provider_contact=provider_contact,
        provider_email=provider_email,
        provider_phone=provider_phone,
        service_type=service_type,
        referral_date=_coerce_date(referral_date),
        notes=notes,
    )
    db.session.add(referral)
    db.session.commit()
    _log_activity(case.id, organization_id, "referral_created", f"Referral to {provider_name} ({service_type})")
    db.session.commit()
    return referral


def list_referrals(case_id: int, organization_id: int) -> List[BeneficiaryReferral]:
    ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    return (
        BeneficiaryReferral.query.filter_by(case_id=case_id)
        .order_by(BeneficiaryReferral.referral_date.desc())
        .all()
    )


def update_referral_status(
    referral_id: int,
    case_id: int,
    organization_id: int,
    status: str,
    *,
    outcome_date: Optional[Any] = None,
    outcome_notes: Optional[str] = None,
) -> BeneficiaryReferral:
    referral = BeneficiaryReferral.query.filter_by(
        id=referral_id, case_id=case_id
    ).first_or_404()
    # Cross-tenant guard
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    referral.status = status
    if outcome_date is not None:
        referral.outcome_date = _coerce_date(outcome_date)
    if outcome_notes is not None:
        referral.outcome_notes = outcome_notes
    db.session.commit()
    _log_activity(case.id, organization_id, "referral_updated", f"Referral {referral_id} status->{status}")
    db.session.commit()
    return referral


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

def create_appointment(
    organization_id: int,
    title: str,
    scheduled_at: Any,
    *,
    case_id: Optional[int] = None,
    beneficiary_id: Optional[int] = None,
    staff_id: Optional[int] = None,
    appointment_type: str = "case_review",
    duration_minutes: int = 60,
    location: Optional[str] = None,
    is_virtual: bool = False,
    meeting_link: Optional[str] = None,
    notes: Optional[str] = None,
) -> BeneficiaryAppointment:
    appt = BeneficiaryAppointment(
        organization_id=organization_id,
        case_id=case_id,
        beneficiary_id=beneficiary_id,
        staff_id=staff_id,
        title=title,
        appointment_type=appointment_type,
        scheduled_at=_coerce_datetime(scheduled_at),
        duration_minutes=duration_minutes,
        location=location,
        is_virtual=is_virtual,
        meeting_link=meeting_link,
        notes=notes,
    )
    db.session.add(appt)
    db.session.commit()
    if case_id:
        case = ProgramCase.query.get(case_id)
        if case and case.organization_id == organization_id:
            _log_activity(case.id, organization_id, "appointment_scheduled", f"{title} at {appt.scheduled_at.isoformat()}")
            db.session.commit()
    return appt


def get_appointment(appointment_id: int, organization_id: int) -> Optional[BeneficiaryAppointment]:
    return BeneficiaryAppointment.query.filter_by(
        id=appointment_id, organization_id=organization_id
    ).first()


def list_appointments(
    organization_id: int,
    *,
    case_id: Optional[int] = None,
    beneficiary_id: Optional[int] = None,
    staff_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[BeneficiaryAppointment]:
    q = BeneficiaryAppointment.query.filter_by(organization_id=organization_id)
    if case_id is not None:
        q = q.filter_by(case_id=case_id)
    if beneficiary_id is not None:
        q = q.filter_by(beneficiary_id=beneficiary_id)
    if staff_id is not None:
        q = q.filter_by(staff_id=staff_id)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(BeneficiaryAppointment.scheduled_at.asc()).all()


def update_appointment(
    appointment_id: int,
    organization_id: int,
    **fields: Any,
) -> BeneficiaryAppointment:
    appt = BeneficiaryAppointment.query.filter_by(
        id=appointment_id, organization_id=organization_id
    ).first_or_404()
    allowed = {
        "title", "appointment_type", "scheduled_at", "duration_minutes",
        "location", "is_virtual", "meeting_link", "status", "notes",
    }
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "scheduled_at":
            value = _coerce_datetime(value)
        setattr(appt, key, value)
    db.session.commit()
    return appt


def cancel_appointment(appointment_id: int, organization_id: int) -> None:
    appt = BeneficiaryAppointment.query.filter_by(
        id=appointment_id, organization_id=organization_id
    ).first_or_404()
    appt.status = "cancelled"
    db.session.commit()


# ---------------------------------------------------------------------------
# Beneficiary profile + timeline
# ---------------------------------------------------------------------------

def beneficiary_profile(beneficiary_id: int, organization_id: int) -> Dict[str, Any]:
    beneficiary = Beneficiary.query.filter_by(
        id=beneficiary_id, organization_id=organization_id
    ).first_or_404()

    cases = ProgramCase.query.filter_by(
        organization_id=organization_id,
        beneficiary_id=beneficiary_id,
    ).all()
    case_ids = [c.id for c in cases]

    total_cases = len(cases)
    open_cases = sum(1 for c in cases if c.status in {"open", "in_progress", "on_hold"})
    closed_cases = sum(1 for c in cases if c.status == "closed")

    avg_progress = (
        round(sum(float(c.progress_percent or 0.0) for c in cases) / total_cases, 2)
        if total_cases
        else 0.0
    )

    latest_assessment: Optional[BeneficiaryAssessment] = None
    if case_ids:
        latest_assessment = (
            BeneficiaryAssessment.query
            .filter(BeneficiaryAssessment.case_id.in_(case_ids))
            .order_by(BeneficiaryAssessment.assessment_date.desc(), BeneficiaryAssessment.id.desc())
            .first()
        )

    service_count = (
        BeneficiaryServiceLog.query
        .filter_by(organization_id=organization_id, beneficiary_id=beneficiary_id)
        .count()
    )

    referral_count = 0
    if case_ids:
        referral_count = (
            BeneficiaryReferral.query
            .filter(BeneficiaryReferral.case_id.in_(case_ids))
            .count()
        )

    now = _utcnow()
    upcoming_appointments = (
        BeneficiaryAppointment.query
        .filter_by(organization_id=organization_id, beneficiary_id=beneficiary_id)
        .filter(BeneficiaryAppointment.scheduled_at >= now)
        .filter(BeneficiaryAppointment.status.in_(["scheduled", "confirmed"]))
        .count()
    )

    return {
        "beneficiary": {
            "id": beneficiary.id,
            "first_name": beneficiary.first_name,
            "last_name": beneficiary.last_name,
            "email": beneficiary.email,
            "phone": beneficiary.phone,
            "program": beneficiary.program,
            "status": beneficiary.status,
            "city": beneficiary.city,
            "country": beneficiary.country,
        },
        "summary": {
            "total_cases": total_cases,
            "open_cases": open_cases,
            "closed_cases": closed_cases,
            "average_progress_percent": avg_progress,
            "service_log_count": service_count,
            "referral_count": referral_count,
            "upcoming_appointments": upcoming_appointments,
        },
        "latest_assessment": (
            {
                "assessment_date": latest_assessment.assessment_date.isoformat(),
                "assessment_type": latest_assessment.assessment_type,
                "risk_level": latest_assessment.risk_level,
                "total_score": latest_assessment.total_score,
            }
            if latest_assessment is not None
            else None
        ),
    }


def beneficiary_timeline(
    beneficiary_id: int,
    organization_id: int,
    *,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    Beneficiary.query.filter_by(
        id=beneficiary_id, organization_id=organization_id
    ).first_or_404()

    cases = ProgramCase.query.filter_by(
        organization_id=organization_id,
        beneficiary_id=beneficiary_id,
    ).all()
    case_ids = [c.id for c in cases]

    events: List[Dict[str, Any]] = []

    if case_ids:
        activities = (
            CaseActivity.query
            .filter(CaseActivity.organization_id == organization_id)
            .filter(CaseActivity.case_id.in_(case_ids))
            .all()
        )
        for item in activities:
            events.append(
                {
                    "event_type": "case_activity",
                    "timestamp": item.created_at,
                    "case_id": item.case_id,
                    "activity_type": item.activity_type,
                    "content": item.content,
                }
            )

        assessments = (
            BeneficiaryAssessment.query
            .filter(BeneficiaryAssessment.organization_id == organization_id)
            .filter(BeneficiaryAssessment.case_id.in_(case_ids))
            .all()
        )
        for item in assessments:
            events.append(
                {
                    "event_type": "assessment",
                    "timestamp": datetime.combine(item.assessment_date, datetime.min.time()),
                    "case_id": item.case_id,
                    "assessment_type": item.assessment_type,
                    "risk_level": item.risk_level,
                    "total_score": item.total_score,
                }
            )

        referrals = (
            BeneficiaryReferral.query
            .filter(BeneficiaryReferral.organization_id == organization_id)
            .filter(BeneficiaryReferral.case_id.in_(case_ids))
            .all()
        )
        for item in referrals:
            events.append(
                {
                    "event_type": "referral",
                    "timestamp": datetime.combine(item.referral_date, datetime.min.time()),
                    "case_id": item.case_id,
                    "provider_name": item.provider_name,
                    "service_type": item.service_type,
                    "status": item.status,
                }
            )

    service_logs = (
        BeneficiaryServiceLog.query
        .filter_by(organization_id=organization_id, beneficiary_id=beneficiary_id)
        .all()
    )
    for item in service_logs:
        events.append(
            {
                "event_type": "service_log",
                "timestamp": item.service_date,
                "case_id": item.case_id,
                "service_type": item.service_type,
                "duration_minutes": item.duration_minutes,
                "service_units": item.service_units,
                "outcome_note": item.outcome_note,
            }
        )

    appointments = (
        BeneficiaryAppointment.query
        .filter_by(organization_id=organization_id, beneficiary_id=beneficiary_id)
        .all()
    )
    for item in appointments:
        events.append(
            {
                "event_type": "appointment",
                "timestamp": item.scheduled_at,
                "case_id": item.case_id,
                "title": item.title,
                "appointment_type": item.appointment_type,
                "status": item.status,
            }
        )

    events.sort(key=lambda row: row.get("timestamp") or datetime.min, reverse=True)
    output: List[Dict[str, Any]] = []
    for row in events[: max(1, min(limit, 500))]:
        payload = dict(row)
        ts = payload.get("timestamp")
        payload["timestamp"] = ts.isoformat() if isinstance(ts, datetime) else None
        output.append(payload)
    return output

