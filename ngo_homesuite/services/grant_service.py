"""Grant management service — full lifecycle from prospect to reporting."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func

from ngo_homesuite.models.core import Grant, GrantDisbursement, db


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_grant(
    organization_id: int,
    funder_name: str,
    title: str,
    *,
    funder_type: str = "foundation",
    funder_contact: Optional[str] = None,
    funder_email: Optional[str] = None,
    description: Optional[str] = None,
    amount_requested: Optional[float] = None,
    currency: str = "USD",
    application_deadline: Optional[date] = None,
    project_id: Optional[int] = None,
    requirements: Optional[str] = None,
    notes: Optional[str] = None,
) -> Grant:
    grant = Grant(
        organization_id=organization_id,
        funder_name=funder_name,
        funder_type=funder_type,
        funder_contact=funder_contact,
        funder_email=funder_email,
        title=title,
        description=description,
        amount_requested=amount_requested,
        currency=currency,
        application_deadline=application_deadline,
        project_id=project_id,
        requirements=requirements,
        notes=notes,
        status="prospect",
    )
    db.session.add(grant)
    db.session.commit()
    return grant


def get_grant(grant_id: int, organization_id: int) -> Optional[Grant]:
    return Grant.query.filter_by(id=grant_id, organization_id=organization_id).first()


def list_grants(
    organization_id: int,
    status: Optional[str] = None,
    upcoming_days: Optional[int] = None,
) -> List[Grant]:
    q = Grant.query.filter_by(organization_id=organization_id)
    if status:
        q = q.filter_by(status=status)
    if upcoming_days is not None:
        cutoff = _today() + timedelta(days=upcoming_days)
        q = q.filter(Grant.application_deadline <= cutoff, Grant.application_deadline >= _today())
    return q.order_by(Grant.application_deadline.asc().nullslast(), Grant.created_at.desc()).all()


def advance_grant_status(grant_id: int, organization_id: int, new_status: str, **kwargs) -> Grant:
    valid_statuses = {"prospect", "in_progress", "submitted", "awarded", "declined", "closed", "reporting"}
    if new_status not in valid_statuses:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of: {valid_statuses}")
    grant = Grant.query.filter_by(id=grant_id, organization_id=organization_id).first_or_404()
    grant.status = new_status
    if new_status == "awarded" and kwargs.get("amount_awarded"):
        grant.amount_awarded = kwargs["amount_awarded"]
        grant.award_date = kwargs.get("award_date", _today())
    if new_status == "submitted":
        grant.submission_date = kwargs.get("submission_date", _today())
    db.session.commit()
    return grant


def update_grant(grant_id: int, organization_id: int, **fields) -> Grant:
    grant = Grant.query.filter_by(id=grant_id, organization_id=organization_id).first_or_404()
    allowed = {
        "funder_name", "funder_type", "funder_contact", "funder_email",
        "title", "description", "amount_requested", "amount_awarded",
        "currency", "application_deadline", "submission_date", "award_date",
        "start_date", "end_date", "report_due_date", "requirements", "notes",
        "project_id", "status",
    }
    for k, v in fields.items():
        if k in allowed:
            setattr(grant, k, v)
    db.session.commit()
    return grant


def delete_grant(grant_id: int, organization_id: int) -> None:
    grant = Grant.query.filter_by(id=grant_id, organization_id=organization_id).first_or_404()
    db.session.delete(grant)
    db.session.commit()


# ---------------------------------------------------------------------------
# Disbursements
# ---------------------------------------------------------------------------

def add_disbursement(
    grant_id: int,
    organization_id: int,
    amount: float,
    received_date: date,
    *,
    currency: str = "USD",
    reference: Optional[str] = None,
    notes: Optional[str] = None,
) -> GrantDisbursement:
    grant = Grant.query.filter_by(id=grant_id, organization_id=organization_id).first_or_404()
    disbursement = GrantDisbursement(
        grant_id=grant.id,
        organization_id=organization_id,
        amount=amount,
        currency=currency,
        received_date=received_date,
        reference=reference,
        notes=notes,
    )
    db.session.add(disbursement)
    db.session.commit()
    return disbursement


def get_disbursements(grant_id: int, organization_id: int) -> List[GrantDisbursement]:
    return GrantDisbursement.query.filter_by(
        grant_id=grant_id, organization_id=organization_id
    ).order_by(GrantDisbursement.received_date.asc()).all()


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def grant_pipeline_summary(organization_id: int) -> dict:
    """Return counts and totals by status for the pipeline overview."""
    rows = (
        db.session.query(
            Grant.status,
            func.count(Grant.id).label("count"),
            func.coalesce(func.sum(Grant.amount_requested), 0).label("total_requested"),
            func.coalesce(func.sum(Grant.amount_awarded), 0).label("total_awarded"),
        )
        .filter_by(organization_id=organization_id)
        .group_by(Grant.status)
        .all()
    )
    return {
        r.status: {
            "count": r.count,
            "total_requested": float(r.total_requested),
            "total_awarded": float(r.total_awarded),
        }
        for r in rows
    }


def grants_due_soon(organization_id: int, within_days: int = 30) -> List[Grant]:
    """Grants whose deadline or report due date falls within `within_days`."""
    cutoff = _today() + timedelta(days=within_days)
    today = _today()
    return (
        Grant.query.filter(
            Grant.organization_id == organization_id,
            Grant.status.in_(["prospect", "in_progress", "awarded", "reporting"]),
            db.or_(
                db.and_(Grant.application_deadline >= today, Grant.application_deadline <= cutoff),
                db.and_(Grant.report_due_date >= today, Grant.report_due_date <= cutoff),
            ),
        )
        .order_by(Grant.application_deadline.asc())
        .all()
    )


def total_disbursed(organization_id: int, grant_id: Optional[int] = None) -> float:
    q = GrantDisbursement.query.filter_by(organization_id=organization_id)
    if grant_id is not None:
        q = q.filter_by(grant_id=grant_id)
    result = db.session.query(func.coalesce(func.sum(GrantDisbursement.amount), 0)).filter(
        GrantDisbursement.organization_id == organization_id
    )
    if grant_id is not None:
        result = result.filter(GrantDisbursement.grant_id == grant_id)
    return float(result.scalar())
