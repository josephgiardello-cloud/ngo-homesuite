"""Grant management service — full lifecycle from prospect to reporting."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import and_, func, or_, select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.models.core import Grant, GrantDisbursement, db


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class GrantNotFound(Exception):
    """Raised when a grant cannot be found for the given org."""

    def __init__(self, grant_id: int):
        super().__init__(f"Grant {grant_id} not found.")
        self.grant_id = grant_id


class InvalidGrantTransition(ValueError):
    """Raised when a requested grant status transition is not permitted."""


# ---------------------------------------------------------------------------

_VALID_GRANT_STATUSES = {"prospect", "in_progress", "submitted", "awarded", "active", "declined", "closed", "reporting"}
_VALID_TRANSITIONS = {
    "prospect": {"in_progress", "submitted", "awarded", "declined", "closed"},
    "in_progress": {"submitted", "awarded", "declined", "closed"},
    "submitted": {"awarded", "declined", "closed"},
    "awarded": {"active", "reporting", "closed"},
    "active": {"reporting", "closed"},
    "reporting": {"closed"},
    "declined": set(),
    "closed": set(),
}


def _grant_disbursed_total(grant_id: int, organization_id: int) -> float:
    stmt = select(func.coalesce(func.sum(GrantDisbursement.amount), 0)).where(
        GrantDisbursement.grant_id == grant_id,
        GrantDisbursement.organization_id == organization_id,
    )
    return float(db.session.scalar(stmt) or 0)


def _validate_currency(code: str) -> str:
    clean = (code or "USD").strip().upper()
    if len(clean) != 3:
        raise ValueError("currency must be a 3-letter ISO code")
    return clean


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
    submission_date: Optional[date] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    report_due_date: Optional[date] = None,
    project_id: Optional[int] = None,
    requirements: Optional[str] = None,
    notes: Optional[str] = None,
) -> Grant:
    if not (funder_name or "").strip():
        raise ValueError("funder_name is required")
    if not (title or "").strip():
        raise ValueError("title is required")
    if amount_requested is not None and float(amount_requested) < 0:
        raise ValueError("amount_requested cannot be negative")

    grant = Grant(
        organization_id=organization_id,
        funder_name=funder_name.strip(),
        funder_type=funder_type,
        funder_contact=funder_contact,
        funder_email=funder_email,
        title=title.strip(),
        description=description,
        amount_requested=amount_requested,
        currency=_validate_currency(currency),
        application_deadline=application_deadline,
        submission_date=submission_date,
        start_date=start_date,
        end_date=end_date,
        report_due_date=report_due_date,
        project_id=project_id,
        requirements=requirements,
        notes=notes,
        status="prospect",
    )
    db.session.add(grant)
    db.session.commit()
    audit(
        "grant.create",
        entity_type="grant",
        entity_id=int(grant.id),
        details={
            "organization_id": int(organization_id),
            "status": grant.status,
            "amount_requested": float(amount_requested or 0),
        },
    )
    return grant


def get_grant(grant_id: int, organization_id: int) -> Optional[Grant]:
    return db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()


def list_grants(
    organization_id: int,
    status: Optional[str] = None,
    upcoming_days: Optional[int] = None,
) -> List[Grant]:
    stmt = select(Grant).where(Grant.organization_id == organization_id)
    if status:
        stmt = stmt.where(Grant.status == status)
    if upcoming_days is not None:
        cutoff = _today() + timedelta(days=upcoming_days)
        stmt = stmt.where(Grant.application_deadline <= cutoff, Grant.application_deadline >= _today())
    stmt = stmt.order_by(Grant.application_deadline.asc().nullslast(), Grant.created_at.desc())
    return list(db.session.scalars(stmt))


def advance_grant_status(grant_id: int, organization_id: int, new_status: str, **kwargs) -> Grant:
    if new_status not in _VALID_GRANT_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of: {_VALID_GRANT_STATUSES}")
    grant = db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()
    if grant is None:
        raise GrantNotFound(grant_id)
    allowed = _VALID_TRANSITIONS.get(grant.status, set())
    if new_status not in allowed:
        raise InvalidGrantTransition(
            f"Cannot transition grant {grant_id} from '{grant.status}' to '{new_status}'. Allowed: {sorted(allowed)}"
        )

    prior_status = grant.status

    if new_status == "awarded":
        if kwargs.get("amount_awarded") is None and (grant.amount_awarded is None or float(grant.amount_awarded) <= 0):
            raise ValueError("amount_awarded is required when transitioning to awarded")

    if new_status == "active" and not grant.amount_awarded and kwargs.get("amount_awarded") is None:
        raise InvalidGrantTransition("Cannot transition to active before award amount is set")

    if new_status == "closed" and grant.amount_awarded:
        disbursed = _grant_disbursed_total(grant.id, organization_id)
        awarded_amount = float(grant.amount_awarded or 0)
        if disbursed + 1e-9 < awarded_amount:
            raise InvalidGrantTransition(
                f"Cannot close grant with outstanding restricted balance. awarded={awarded_amount:.2f}, disbursed={disbursed:.2f}"
            )

    grant.status = new_status
    if new_status == "awarded" and kwargs.get("amount_awarded"):
        if float(kwargs["amount_awarded"]) < 0:
            raise ValueError("amount_awarded cannot be negative")
        grant.amount_awarded = kwargs["amount_awarded"]
        grant.award_date = kwargs.get("award_date", _today())
    if new_status == "submitted":
        grant.submission_date = kwargs.get("submission_date", _today())
    db.session.commit()
    audit(
        "grant.status.transition",
        entity_type="grant",
        entity_id=int(grant.id),
        details={
            "organization_id": int(organization_id),
            "from_status": prior_status,
            "to_status": new_status,
        },
    )
    return grant


def update_grant(grant_id: int, organization_id: int, **fields) -> Grant:
    grant = db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()
    if grant is None:
        raise GrantNotFound(grant_id)
    allowed = {
        "funder_name", "funder_type", "funder_contact", "funder_email",
        "title", "description", "amount_requested", "amount_awarded",
        "currency", "application_deadline", "submission_date", "award_date",
        "start_date", "end_date", "report_due_date", "requirements", "notes",
        "project_id", "status",
    }
    for k, v in fields.items():
        if k in allowed:
            if k in {"amount_requested", "amount_awarded"} and v is not None and float(v) < 0:
                raise ValueError(f"{k} cannot be negative")
            if k == "currency":
                v = _validate_currency(str(v))
            setattr(grant, k, v)

    if grant.status == "closed" and grant.amount_awarded:
        disbursed = _grant_disbursed_total(grant.id, organization_id)
        if disbursed + 1e-9 < float(grant.amount_awarded or 0):
            raise ValueError("cannot set status=closed while restricted balance remains")

    db.session.commit()
    audit(
        "grant.update",
        entity_type="grant",
        entity_id=int(grant.id),
        details={
            "organization_id": int(organization_id),
            "updated_fields": sorted([k for k in fields.keys() if k in allowed]),
        },
    )
    return grant


def delete_grant(grant_id: int, organization_id: int) -> None:
    grant = db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()
    if grant is None:
        raise GrantNotFound(grant_id)
    db.session.delete(grant)
    db.session.commit()
    audit(
        "grant.delete",
        entity_type="grant",
        entity_id=int(grant_id),
        details={"organization_id": int(organization_id)},
    )


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
    if amount <= 0:
        raise ValueError("disbursement amount must be positive")
    grant = db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()
    if grant is None:
        raise GrantNotFound(grant_id)

    if grant.status not in {"awarded", "active", "reporting"}:
        raise ValueError("cannot disburse before grant is awarded/active/reporting")

    if grant.amount_awarded is not None:
        already_disbursed = _grant_disbursed_total(grant.id, organization_id)
        projected_total = already_disbursed + float(amount)
        if projected_total > float(grant.amount_awarded) + 1e-9:
            raise ValueError("disbursement exceeds awarded amount")

    disbursement = GrantDisbursement(
        grant_id=grant.id,
        organization_id=organization_id,
        amount=amount,
        currency=_validate_currency(currency),
        received_date=received_date,
        reference=reference,
        notes=notes,
    )
    db.session.add(disbursement)
    db.session.commit()
    audit(
        "grant.disbursement.add",
        entity_type="grant",
        entity_id=int(grant.id),
        details={
            "organization_id": int(organization_id),
            "disbursement_id": int(disbursement.id),
            "amount": float(amount),
            "received_date": received_date.isoformat(),
        },
    )
    return disbursement


def get_disbursements(grant_id: int, organization_id: int) -> List[GrantDisbursement]:
    stmt = select(GrantDisbursement).where(
        GrantDisbursement.grant_id == grant_id,
        GrantDisbursement.organization_id == organization_id,
    ).order_by(GrantDisbursement.received_date.asc())
    return list(db.session.scalars(stmt))


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def grant_pipeline_summary(organization_id: int) -> dict:
    """Return counts and totals by status for the pipeline overview."""
    rows = db.session.connection().exec_driver_sql(
        str(select(
            Grant.status,
            func.count(Grant.id).label("count"),
            func.coalesce(func.sum(Grant.amount_requested), 0).label("total_requested"),
            func.coalesce(func.sum(Grant.amount_awarded), 0).label("total_awarded"),
        ).where(Grant.organization_id == organization_id).group_by(Grant.status).compile(compile_kwargs={"literal_binds": True}))
    ).all()
    return {
        status: {
            "count": count,
            "total_requested": float(total_requested),
            "total_awarded": float(total_awarded),
        }
        for status, count, total_requested, total_awarded in rows
    }


def grants_due_soon(organization_id: int, within_days: int = 30) -> List[Grant]:
    """Grants whose deadline or report due date falls within `within_days`."""
    cutoff = _today() + timedelta(days=within_days)
    today = _today()
    stmt = select(Grant).where(
        Grant.organization_id == organization_id,
        Grant.status.in_(["prospect", "in_progress", "awarded", "reporting"]),
        or_(
            and_(Grant.application_deadline >= today, Grant.application_deadline <= cutoff),
            and_(Grant.report_due_date >= today, Grant.report_due_date <= cutoff),
        ),
    ).order_by(Grant.application_deadline.asc())
    return list(db.session.scalars(stmt))


def total_disbursed(organization_id: int, grant_id: Optional[int] = None) -> float:
    stmt = select(func.coalesce(func.sum(GrantDisbursement.amount), 0)).where(
        GrantDisbursement.organization_id == organization_id,
    )
    if grant_id is not None:
        stmt = stmt.where(GrantDisbursement.grant_id == grant_id)
    return float(db.session.scalar(stmt) or 0)


def grant_calendar_events(organization_id: int, within_days: int = 120) -> List[dict]:
    """Flatten grant lifecycle milestones into light calendar events."""
    today = _today()
    cutoff = today + timedelta(days=max(1, within_days))

    grants = list(
        db.session.scalars(
            select(Grant)
            .where(Grant.organization_id == organization_id)
            .order_by(Grant.application_deadline.asc().nullslast(), Grant.created_at.desc())
        )
    )

    events: List[dict] = []
    for grant in grants:
        milestones = [
            ("application_deadline", grant.application_deadline, "Application deadline"),
            ("submission_date", grant.submission_date, "Proposal submitted"),
            ("award_date", grant.award_date, "Awarded"),
            ("report_due_date", grant.report_due_date, "Report due"),
            ("start_date", grant.start_date, "Grant period starts"),
            ("end_date", grant.end_date, "Grant period ends"),
        ]

        for milestone_type, milestone_date, label in milestones:
            if milestone_date is None:
                continue
            if milestone_date < today or milestone_date > cutoff:
                continue

            events.append(
                {
                    "grant_id": int(grant.id),
                    "title": grant.title,
                    "funder_name": grant.funder_name,
                    "status": grant.status,
                    "event_type": milestone_type,
                    "event_label": label,
                    "event_date": milestone_date.isoformat(),
                }
            )

    events.sort(key=lambda e: (e["event_date"], e["title"]))
    return events


def restricted_funding_summary(organization_id: int) -> dict:
    """Summarize awarded grant balances as restricted funding visibility."""
    grants = list(
        db.session.scalars(
            select(Grant)
            .where(
                Grant.organization_id == organization_id,
                Grant.status.in_(["awarded", "reporting", "closed"]),
                Grant.amount_awarded.is_not(None),
                Grant.amount_awarded > 0,
            )
            .order_by(Grant.award_date.desc().nullslast(), Grant.created_at.desc())
        )
    )

    grant_rows: List[dict] = []
    total_awarded = 0.0
    total_disbursed_amount = 0.0
    total_restricted_remaining = 0.0

    for grant in grants:
        awarded = float(grant.amount_awarded or 0)
        disbursed = total_disbursed(organization_id, grant_id=int(grant.id))
        remaining = max(0.0, awarded - disbursed)

        total_awarded += awarded
        total_disbursed_amount += disbursed
        total_restricted_remaining += remaining

        grant_rows.append(
            {
                "grant_id": int(grant.id),
                "title": grant.title,
                "funder_name": grant.funder_name,
                "status": grant.status,
                "award_date": grant.award_date.isoformat() if grant.award_date else None,
                "report_due_date": grant.report_due_date.isoformat() if grant.report_due_date else None,
                "amount_awarded": awarded,
                "amount_disbursed": disbursed,
                "outstanding_balance": remaining,
            }
        )

    return {
        "grant_count": len(grant_rows),
        "total_awarded": total_awarded,
        "total_disbursed": total_disbursed_amount,
        "total_restricted_remaining": total_restricted_remaining,
        "grants": grant_rows,
    }
