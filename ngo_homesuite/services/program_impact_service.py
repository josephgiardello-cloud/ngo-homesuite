"""Program Impact / Case Management service.

CiviCRM-style case tracking: each ProgramCase has an immutable activity log
and can track qualitative + quantitative outcomes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ngo_homesuite.models.core import CaseActivity, ProgramCase, db

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    next_review_date: Optional[Any] = None,
    actor_id: Optional[int] = None,
) -> ProgramCase:
    case = ProgramCase(
        organization_id=organization_id,
        title=title,
        donor_id=donor_id,
        project_id=project_id,
        case_type=case_type,
        description=description,
        outcome_metric=outcome_metric,
        next_review_date=next_review_date,
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


def delete_case(case_id: int, organization_id: int) -> None:
    case = ProgramCase.query.filter_by(id=case_id, organization_id=organization_id).first_or_404()
    db.session.delete(case)
    db.session.commit()


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
