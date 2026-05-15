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
