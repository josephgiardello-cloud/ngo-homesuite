"""Grant management service — full lifecycle from prospect to reporting."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import and_, func, or_, select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.models.core import (
    Expense,
    Grant,
    GrantBudgetLine,
    GrantDisbursement,
    GrantExpenseAllocation,
    GrantOpportunity,
    GrantProposal,
    db,
)


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


class GrantAllocationError(ValueError):
    """Raised when grant expense allocation violates budget or tenant constraints."""


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
_VALID_OPPORTUNITY_STATUSES = {"identified", "qualified", "in_progress", "submitted", "awarded", "declined", "archived"}
_VALID_PROPOSAL_OUTCOMES = {"draft", "submitted", "awarded", "declined", "withdrawn"}


def _grant_disbursed_total(grant_id: int, organization_id: int) -> float:
    stmt = select(func.coalesce(func.sum(GrantDisbursement.amount), 0)).where(
        GrantDisbursement.grant_id == grant_id,
        GrantDisbursement.organization_id == organization_id,
    )
    return float(db.session.scalar(stmt) or 0)


def _grant_budget_allocated_total(grant_id: int, organization_id: int) -> float:
    stmt = select(func.coalesce(func.sum(GrantBudgetLine.allocated_amount), 0)).where(
        GrantBudgetLine.grant_id == grant_id,
        GrantBudgetLine.organization_id == organization_id,
    )
    return float(db.session.scalar(stmt) or 0)


def _grant_spent_total(grant_id: int, organization_id: int) -> float:
    stmt = select(func.coalesce(func.sum(GrantExpenseAllocation.amount), 0)).where(
        GrantExpenseAllocation.grant_id == grant_id,
        GrantExpenseAllocation.organization_id == organization_id,
    )
    return float(db.session.scalar(stmt) or 0)


def _grant_has_budget_lines(grant_id: int, organization_id: int) -> bool:
    stmt = select(func.count(GrantBudgetLine.id)).where(
        GrantBudgetLine.grant_id == grant_id,
        GrantBudgetLine.organization_id == organization_id,
    )
    return int(db.session.scalar(stmt) or 0) > 0


def _grant_budget_remaining(grant_id: int, organization_id: int) -> float:
    allocated = _grant_budget_allocated_total(grant_id, organization_id)
    spent = _grant_spent_total(grant_id, organization_id)
    return max(0.0, allocated - spent)


def _normalize_budget_category(category: str) -> str:
    normalized = (category or "").strip().lower().replace(" ", "_")
    if not normalized:
        raise GrantAllocationError("budget category is required")
    if len(normalized) > 80:
        raise GrantAllocationError("budget category is too long")
    return normalized


def _expected_version_check(current_version: int, expected_version: Optional[int], *, entity: str) -> None:
    if expected_version is not None and int(expected_version) != int(current_version):
        raise GrantAllocationError(
            f"{entity} version mismatch (expected {expected_version}, current {current_version})"
        )


def _compute_probability_weighted_amount(amount_min: Optional[float], amount_max: Optional[float], probability: float) -> float:
    if amount_min is None and amount_max is None:
        return 0.0
    if amount_min is None:
        base = float(amount_max or 0)
    elif amount_max is None:
        base = float(amount_min or 0)
    else:
        base = (float(amount_min) + float(amount_max)) / 2.0
    return round(base * float(probability), 2)


def _validate_probability(probability: float) -> float:
    value = float(probability)
    if value < 0 or value > 1:
        raise ValueError("probability must be between 0 and 1")
    return value


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
        awarded_amount = float(grant.amount_awarded or 0)
        if _grant_has_budget_lines(grant.id, organization_id):
            remaining_budget = _grant_budget_remaining(grant.id, organization_id)
            if remaining_budget > 1e-9:
                raise InvalidGrantTransition(
                    f"Cannot close grant with outstanding restricted balance. remaining_budget={remaining_budget:.2f}"
                )
        else:
            disbursed = _grant_disbursed_total(grant.id, organization_id)
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


def create_budget_line(
    grant_id: int,
    organization_id: int,
    *,
    category: str,
    allocated_amount: float,
    line_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> GrantBudgetLine:
    if allocated_amount <= 0:
        raise GrantAllocationError("allocated_amount must be positive")

    grant = db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()
    if grant is None:
        raise GrantNotFound(grant_id)

    normalized_category = _normalize_budget_category(category)
    existing = db.session.scalars(
        select(GrantBudgetLine).where(
            GrantBudgetLine.grant_id == grant_id,
            GrantBudgetLine.organization_id == organization_id,
            GrantBudgetLine.category == normalized_category,
        ).limit(1)
    ).first()
    if existing is not None:
        raise GrantAllocationError(f"budget line category '{normalized_category}' already exists for this grant")

    current_allocated = _grant_budget_allocated_total(grant_id, organization_id)
    projected_allocated = current_allocated + float(allocated_amount)
    if grant.amount_awarded is not None and projected_allocated > float(grant.amount_awarded) + 1e-9:
        raise GrantAllocationError("budget lines cannot exceed amount_awarded")

    budget_line = GrantBudgetLine(
        grant_id=grant_id,
        organization_id=organization_id,
        category=normalized_category,
        line_name=(line_name or normalized_category.replace("_", " ").title()).strip(),
        allocated_amount=float(allocated_amount),
        notes=(notes or "").strip() or None,
    )
    db.session.add(budget_line)
    db.session.commit()
    audit(
        "grant.budget_line.create",
        entity_type="grant",
        entity_id=int(grant_id),
        details={
            "organization_id": int(organization_id),
            "budget_line_id": int(budget_line.id),
            "category": normalized_category,
            "allocated_amount": float(allocated_amount),
        },
    )
    return budget_line


def update_budget_line(
    grant_id: int,
    organization_id: int,
    budget_line_id: int,
    *,
    allocated_amount: Optional[float] = None,
    line_name: Optional[str] = None,
    notes: Optional[str] = None,
    expected_version: Optional[int] = None,
) -> GrantBudgetLine:
    budget_line = db.session.scalars(
        select(GrantBudgetLine).where(
            GrantBudgetLine.id == budget_line_id,
            GrantBudgetLine.grant_id == grant_id,
            GrantBudgetLine.organization_id == organization_id,
        ).limit(1)
    ).first()
    if budget_line is None:
        raise GrantAllocationError("budget line not found for grant/organization")

    _expected_version_check(int(budget_line.version_id), expected_version, entity="budget line")

    current_spent = float(
        db.session.scalar(
            select(func.coalesce(func.sum(GrantExpenseAllocation.amount), 0)).where(
                GrantExpenseAllocation.budget_line_id == budget_line.id,
                GrantExpenseAllocation.organization_id == organization_id,
            )
        )
        or 0
    )

    if allocated_amount is not None:
        new_allocated = float(allocated_amount)
        if new_allocated <= 0:
            raise GrantAllocationError("allocated_amount must be positive")
        if new_allocated + 1e-9 < current_spent:
            raise GrantAllocationError("allocated_amount cannot be lower than current allocated expenses")

        grant = db.session.scalars(
            select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
        ).first()
        if grant is None:
            raise GrantNotFound(grant_id)

        total_other_lines = float(
            db.session.scalar(
                select(func.coalesce(func.sum(GrantBudgetLine.allocated_amount), 0)).where(
                    GrantBudgetLine.grant_id == grant_id,
                    GrantBudgetLine.organization_id == organization_id,
                    GrantBudgetLine.id != budget_line.id,
                )
            )
            or 0
        )
        projected_total = total_other_lines + new_allocated
        if grant.amount_awarded is not None and projected_total > float(grant.amount_awarded) + 1e-9:
            raise GrantAllocationError("budget lines cannot exceed amount_awarded")
        budget_line.allocated_amount = new_allocated

    if line_name is not None:
        clean_name = line_name.strip()
        if not clean_name:
            raise GrantAllocationError("line_name cannot be empty")
        budget_line.line_name = clean_name

    if notes is not None:
        budget_line.notes = notes.strip() or None

    db.session.commit()
    audit(
        "grant.budget_line.update",
        entity_type="grant",
        entity_id=int(grant_id),
        details={
            "organization_id": int(organization_id),
            "budget_line_id": int(budget_line.id),
            "category": budget_line.category,
            "allocated_amount": float(budget_line.allocated_amount),
            "version_id": int(budget_line.version_id),
        },
    )
    return budget_line


def delete_budget_line(
    grant_id: int,
    organization_id: int,
    budget_line_id: int,
    *,
    expected_version: Optional[int] = None,
) -> None:
    budget_line = db.session.scalars(
        select(GrantBudgetLine).where(
            GrantBudgetLine.id == budget_line_id,
            GrantBudgetLine.grant_id == grant_id,
            GrantBudgetLine.organization_id == organization_id,
        ).limit(1)
    ).first()
    if budget_line is None:
        raise GrantAllocationError("budget line not found for grant/organization")

    _expected_version_check(int(budget_line.version_id), expected_version, entity="budget line")

    allocation_count = int(
        db.session.scalar(
            select(func.count(GrantExpenseAllocation.id)).where(
                GrantExpenseAllocation.budget_line_id == budget_line.id,
                GrantExpenseAllocation.organization_id == organization_id,
            )
        )
        or 0
    )
    if allocation_count > 0:
        raise GrantAllocationError("cannot delete budget line with existing allocations")

    category = budget_line.category
    db.session.delete(budget_line)
    db.session.commit()
    audit(
        "grant.budget_line.delete",
        entity_type="grant",
        entity_id=int(grant_id),
        details={
            "organization_id": int(organization_id),
            "budget_line_id": int(budget_line_id),
            "category": category,
        },
    )


def allocate_expense_to_budget_line(
    grant_id: int,
    organization_id: int,
    *,
    expense_id: int,
    category: str,
    supporting_document_ref: Optional[str] = None,
    commit: bool = True,
) -> GrantExpenseAllocation:
    grant = db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()
    if grant is None:
        raise GrantNotFound(grant_id)

    expense = db.session.scalars(
        select(Expense).where(Expense.id == expense_id, Expense.organization_id == organization_id).limit(1)
    ).first()
    if expense is None:
        raise GrantAllocationError("expense not found for organization")

    normalized_category = _normalize_budget_category(category)
    budget_line = db.session.scalars(
        select(GrantBudgetLine).where(
            GrantBudgetLine.grant_id == grant_id,
            GrantBudgetLine.organization_id == organization_id,
            GrantBudgetLine.category == normalized_category,
        ).limit(1)
    ).first()
    if budget_line is None:
        raise GrantAllocationError(f"no budget line configured for category '{normalized_category}'")

    existing = db.session.scalars(
        select(GrantExpenseAllocation).where(
            GrantExpenseAllocation.expense_id == expense_id,
            GrantExpenseAllocation.organization_id == organization_id,
        ).limit(1)
    ).first()
    if existing is not None:
        raise GrantAllocationError("expense already allocated to a grant budget line")

    amount = float(expense.amount or 0)
    if amount <= 0:
        raise GrantAllocationError("expense amount must be positive")

    line_spent_stmt = select(func.coalesce(func.sum(GrantExpenseAllocation.amount), 0)).where(
        GrantExpenseAllocation.budget_line_id == budget_line.id,
        GrantExpenseAllocation.organization_id == organization_id,
    )
    line_spent = float(db.session.scalar(line_spent_stmt) or 0)
    line_remaining = float(budget_line.allocated_amount or 0) - line_spent
    if amount > line_remaining + 1e-9:
        raise GrantAllocationError("expense allocation exceeds remaining budget line balance")

    if grant.amount_awarded is not None:
        spent_total = _grant_spent_total(grant_id, organization_id)
        if spent_total + amount > float(grant.amount_awarded) + 1e-9:
            raise GrantAllocationError("expense allocation exceeds awarded amount")

    allocation = GrantExpenseAllocation(
        grant_id=grant_id,
        budget_line_id=budget_line.id,
        expense_id=expense_id,
        organization_id=organization_id,
        amount=amount,
        category=normalized_category,
        supporting_document_ref=(supporting_document_ref or "").strip() or None,
    )
    db.session.add(allocation)
    db.session.flush()

    if commit:
        db.session.commit()

    audit(
        "grant.allocation.create",
        entity_type="grant",
        entity_id=int(grant_id),
        details={
            "organization_id": int(organization_id),
            "allocation_id": int(allocation.id),
            "expense_id": int(expense_id),
            "budget_line_id": int(budget_line.id),
            "category": normalized_category,
            "amount": amount,
        },
    )
    return allocation


def update_allocation(
    grant_id: int,
    organization_id: int,
    allocation_id: int,
    *,
    amount: Optional[float] = None,
    supporting_document_ref: Optional[str] = None,
    expected_version: Optional[int] = None,
) -> GrantExpenseAllocation:
    allocation = db.session.scalars(
        select(GrantExpenseAllocation).where(
            GrantExpenseAllocation.id == allocation_id,
            GrantExpenseAllocation.grant_id == grant_id,
            GrantExpenseAllocation.organization_id == organization_id,
        ).limit(1)
    ).first()
    if allocation is None:
        raise GrantAllocationError("allocation not found for grant/organization")

    _expected_version_check(int(allocation.version_id), expected_version, entity="allocation")

    if amount is not None:
        new_amount = float(amount)
        if new_amount <= 0:
            raise GrantAllocationError("allocation amount must be positive")

        expense_amount = float(allocation.expense.amount or 0)
        if new_amount > expense_amount + 1e-9:
            raise GrantAllocationError("allocation amount cannot exceed source expense amount")

        line_spent_excluding_self = float(
            db.session.scalar(
                select(func.coalesce(func.sum(GrantExpenseAllocation.amount), 0)).where(
                    GrantExpenseAllocation.budget_line_id == allocation.budget_line_id,
                    GrantExpenseAllocation.organization_id == organization_id,
                    GrantExpenseAllocation.id != allocation.id,
                )
            )
            or 0
        )
        line_remaining = float(allocation.budget_line.allocated_amount or 0) - line_spent_excluding_self
        if new_amount > line_remaining + 1e-9:
            raise GrantAllocationError("allocation amount exceeds remaining budget line balance")

        grant = db.session.scalars(
            select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
        ).first()
        if grant is None:
            raise GrantNotFound(grant_id)
        if grant.amount_awarded is not None:
            spent_excluding_self = _grant_spent_total(grant_id, organization_id) - float(allocation.amount or 0)
            if spent_excluding_self + new_amount > float(grant.amount_awarded) + 1e-9:
                raise GrantAllocationError("allocation amount exceeds awarded amount")

        allocation.amount = new_amount

    if supporting_document_ref is not None:
        allocation.supporting_document_ref = supporting_document_ref.strip() or None

    db.session.commit()
    audit(
        "grant.allocation.update",
        entity_type="grant",
        entity_id=int(grant_id),
        details={
            "organization_id": int(organization_id),
            "allocation_id": int(allocation.id),
            "budget_line_id": int(allocation.budget_line_id),
            "expense_id": int(allocation.expense_id),
            "amount": float(allocation.amount),
            "version_id": int(allocation.version_id),
        },
    )
    return allocation


def delete_allocation(
    grant_id: int,
    organization_id: int,
    allocation_id: int,
    *,
    expected_version: Optional[int] = None,
) -> None:
    allocation = db.session.scalars(
        select(GrantExpenseAllocation).where(
            GrantExpenseAllocation.id == allocation_id,
            GrantExpenseAllocation.grant_id == grant_id,
            GrantExpenseAllocation.organization_id == organization_id,
        ).limit(1)
    ).first()
    if allocation is None:
        raise GrantAllocationError("allocation not found for grant/organization")

    _expected_version_check(int(allocation.version_id), expected_version, entity="allocation")

    expense_id = int(allocation.expense_id)
    budget_line_id = int(allocation.budget_line_id)
    db.session.delete(allocation)
    db.session.commit()
    audit(
        "grant.allocation.delete",
        entity_type="grant",
        entity_id=int(grant_id),
        details={
            "organization_id": int(organization_id),
            "allocation_id": int(allocation_id),
            "budget_line_id": budget_line_id,
            "expense_id": expense_id,
        },
    )


def list_budget_lines(grant_id: int, organization_id: int) -> List[GrantBudgetLine]:
    stmt = select(GrantBudgetLine).where(
        GrantBudgetLine.grant_id == grant_id,
        GrantBudgetLine.organization_id == organization_id,
    ).order_by(GrantBudgetLine.category.asc())
    return list(db.session.scalars(stmt))


def create_opportunity(
    organization_id: int,
    *,
    funder_name: str,
    program_name: str,
    title: str,
    deadline: Optional[date] = None,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    probability: float = 0.0,
    status: str = "identified",
    notes: Optional[str] = None,
) -> GrantOpportunity:
    if not funder_name.strip():
        raise ValueError("funder_name is required")
    if not program_name.strip():
        raise ValueError("program_name is required")
    if not title.strip():
        raise ValueError("title is required")
    if status not in _VALID_OPPORTUNITY_STATUSES:
        raise ValueError(f"invalid opportunity status '{status}'")
    if amount_min is not None and float(amount_min) < 0:
        raise ValueError("amount_min cannot be negative")
    if amount_max is not None and float(amount_max) < 0:
        raise ValueError("amount_max cannot be negative")
    if amount_min is not None and amount_max is not None and float(amount_max) < float(amount_min):
        raise ValueError("amount_max must be greater than or equal to amount_min")

    probability_value = _validate_probability(probability)
    weighted = _compute_probability_weighted_amount(amount_min, amount_max, probability_value)

    opportunity = GrantOpportunity(
        organization_id=organization_id,
        funder_name=funder_name.strip(),
        program_name=program_name.strip(),
        title=title.strip(),
        deadline=deadline,
        amount_min=float(amount_min) if amount_min is not None else None,
        amount_max=float(amount_max) if amount_max is not None else None,
        probability=probability_value,
        probability_weighted_amount=weighted,
        status=status,
        notes=(notes or "").strip() or None,
    )
    db.session.add(opportunity)
    db.session.commit()
    audit(
        "grant.opportunity.create",
        entity_type="grant_opportunity",
        entity_id=int(opportunity.id),
        details={
            "organization_id": int(organization_id),
            "status": opportunity.status,
            "probability": float(opportunity.probability),
            "probability_weighted_amount": float(opportunity.probability_weighted_amount),
        },
    )
    return opportunity


def list_opportunities(organization_id: int, *, status: Optional[str] = None) -> List[GrantOpportunity]:
    stmt = select(GrantOpportunity).where(GrantOpportunity.organization_id == organization_id)
    if status:
        stmt = stmt.where(GrantOpportunity.status == status)
    stmt = stmt.order_by(GrantOpportunity.deadline.asc().nullslast(), GrantOpportunity.created_at.desc())
    return list(db.session.scalars(stmt))


def update_opportunity(
    opportunity_id: int,
    organization_id: int,
    **fields,
) -> GrantOpportunity:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == opportunity_id,
            GrantOpportunity.organization_id == organization_id,
        ).limit(1)
    ).first()
    if opportunity is None:
        raise GrantAllocationError("opportunity not found for organization")

    allowed = {
        "funder_name", "program_name", "title", "deadline", "amount_min", "amount_max",
        "probability", "status", "notes", "expected_version",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}

    _expected_version_check(int(opportunity.version_id), updates.get("expected_version"), entity="opportunity")

    if "status" in updates and updates["status"] not in _VALID_OPPORTUNITY_STATUSES:
        raise ValueError(f"invalid opportunity status '{updates['status']}'")
    if "amount_min" in updates and updates["amount_min"] is not None and float(updates["amount_min"]) < 0:
        raise ValueError("amount_min cannot be negative")
    if "amount_max" in updates and updates["amount_max"] is not None and float(updates["amount_max"]) < 0:
        raise ValueError("amount_max cannot be negative")

    for key in ["funder_name", "program_name", "title", "deadline", "amount_min", "amount_max", "status", "notes"]:
        if key in updates:
            value = updates[key]
            if key in {"funder_name", "program_name", "title"}:
                clean = str(value or "").strip()
                if not clean:
                    raise ValueError(f"{key} is required")
                setattr(opportunity, key, clean)
            elif key == "notes":
                opportunity.notes = str(value or "").strip() or None
            elif key in {"amount_min", "amount_max"}:
                setattr(opportunity, key, float(value) if value is not None else None)
            else:
                setattr(opportunity, key, value)

    if opportunity.amount_min is not None and opportunity.amount_max is not None:
        if float(opportunity.amount_max) < float(opportunity.amount_min):
            raise ValueError("amount_max must be greater than or equal to amount_min")

    if "probability" in updates:
        opportunity.probability = _validate_probability(float(updates["probability"]))

    opportunity.probability_weighted_amount = _compute_probability_weighted_amount(
        opportunity.amount_min,
        opportunity.amount_max,
        float(opportunity.probability or 0),
    )

    db.session.commit()
    audit(
        "grant.opportunity.update",
        entity_type="grant_opportunity",
        entity_id=int(opportunity.id),
        details={
            "organization_id": int(organization_id),
            "status": opportunity.status,
            "probability": float(opportunity.probability or 0),
            "version_id": int(opportunity.version_id),
        },
    )
    return opportunity


def create_proposal(
    opportunity_id: int,
    organization_id: int,
    *,
    amount_requested: Optional[float] = None,
    narrative_summary: Optional[str] = None,
    document_ref: Optional[str] = None,
    notes: Optional[str] = None,
) -> GrantProposal:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == opportunity_id,
            GrantOpportunity.organization_id == organization_id,
        ).limit(1)
    ).first()
    if opportunity is None:
        raise GrantAllocationError("opportunity not found for organization")

    if amount_requested is not None and float(amount_requested) < 0:
        raise ValueError("amount_requested cannot be negative")

    next_version = int(
        db.session.scalar(
            select(func.coalesce(func.max(GrantProposal.version_number), 0)).where(
                GrantProposal.opportunity_id == opportunity_id,
                GrantProposal.organization_id == organization_id,
            )
        )
        or 0
    ) + 1

    proposal = GrantProposal(
        opportunity_id=opportunity_id,
        organization_id=organization_id,
        version_number=next_version,
        amount_requested=float(amount_requested) if amount_requested is not None else None,
        narrative_summary=(narrative_summary or "").strip() or None,
        document_ref=(document_ref or "").strip() or None,
        notes=(notes or "").strip() or None,
        outcome="draft",
    )
    db.session.add(proposal)
    db.session.commit()
    audit(
        "grant.proposal.create",
        entity_type="grant_proposal",
        entity_id=int(proposal.id),
        details={
            "organization_id": int(organization_id),
            "opportunity_id": int(opportunity_id),
            "version_number": int(proposal.version_number),
        },
    )
    return proposal


def submit_proposal(
    proposal_id: int,
    organization_id: int,
    *,
    submission_date: date,
    document_ref: Optional[str] = None,
) -> GrantProposal:
    proposal = db.session.scalars(
        select(GrantProposal).where(
            GrantProposal.id == proposal_id,
            GrantProposal.organization_id == organization_id,
        ).limit(1)
    ).first()
    if proposal is None:
        raise GrantAllocationError("proposal not found for organization")

    if not proposal.narrative_summary:
        raise ValueError("cannot submit proposal without narrative_summary")
    if proposal.amount_requested is None or float(proposal.amount_requested) <= 0:
        raise ValueError("cannot submit proposal without amount_requested")

    effective_doc_ref = (document_ref or proposal.document_ref or "").strip()
    if not effective_doc_ref:
        raise ValueError("cannot submit proposal without document_ref")

    proposal.submission_date = submission_date
    proposal.document_ref = effective_doc_ref
    proposal.outcome = "submitted"
    proposal.opportunity.status = "submitted"
    db.session.commit()
    audit(
        "grant.proposal.submit",
        entity_type="grant_proposal",
        entity_id=int(proposal.id),
        details={
            "organization_id": int(organization_id),
            "opportunity_id": int(proposal.opportunity_id),
            "submission_date": submission_date.isoformat(),
        },
    )
    return proposal


def set_proposal_outcome(
    proposal_id: int,
    organization_id: int,
    *,
    outcome: str,
) -> GrantProposal:
    if outcome not in _VALID_PROPOSAL_OUTCOMES:
        raise ValueError(f"invalid proposal outcome '{outcome}'")

    proposal = db.session.scalars(
        select(GrantProposal).where(
            GrantProposal.id == proposal_id,
            GrantProposal.organization_id == organization_id,
        ).limit(1)
    ).first()
    if proposal is None:
        raise GrantAllocationError("proposal not found for organization")

    proposal.outcome = outcome
    if outcome == "awarded":
        proposal.opportunity.status = "awarded"
    elif outcome == "declined":
        proposal.opportunity.status = "declined"
    db.session.commit()
    audit(
        "grant.proposal.outcome",
        entity_type="grant_proposal",
        entity_id=int(proposal.id),
        details={
            "organization_id": int(organization_id),
            "opportunity_id": int(proposal.opportunity_id),
            "outcome": outcome,
        },
    )
    return proposal


def convert_opportunity_to_grant(
    opportunity_id: int,
    organization_id: int,
    *,
    amount_awarded: float,
    award_date: Optional[date] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Grant:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == opportunity_id,
            GrantOpportunity.organization_id == organization_id,
        ).limit(1)
    ).first()
    if opportunity is None:
        raise GrantAllocationError("opportunity not found for organization")

    if opportunity.awarded_grant_id is not None:
        raise GrantAllocationError("opportunity already linked to awarded grant")
    if float(amount_awarded) <= 0:
        raise ValueError("amount_awarded must be positive")

    grant = create_grant(
        organization_id=organization_id,
        funder_name=opportunity.funder_name,
        title=opportunity.title,
        amount_requested=opportunity.amount_max or opportunity.amount_min,
        application_deadline=opportunity.deadline,
        start_date=start_date,
        end_date=end_date,
        notes=opportunity.notes,
    )
    advance_grant_status(grant.id, organization_id, new_status="submitted")
    advance_grant_status(
        grant.id,
        organization_id,
        new_status="awarded",
        amount_awarded=float(amount_awarded),
        award_date=award_date or _today(),
    )

    opportunity.awarded_grant_id = int(grant.id)
    opportunity.status = "awarded"
    db.session.commit()
    audit(
        "grant.opportunity.convert",
        entity_type="grant_opportunity",
        entity_id=int(opportunity.id),
        details={
            "organization_id": int(organization_id),
            "grant_id": int(grant.id),
            "amount_awarded": float(amount_awarded),
        },
    )
    return grant


def opportunity_forecast_summary(organization_id: int) -> dict:
    active_statuses = ["identified", "qualified", "in_progress", "submitted"]
    opportunities = list(
        db.session.scalars(
            select(GrantOpportunity)
            .where(GrantOpportunity.organization_id == organization_id, GrantOpportunity.status.in_(active_statuses))
            .order_by(GrantOpportunity.deadline.asc().nullslast())
        )
    )

    pipeline_count = len(opportunities)
    pipeline_amount = 0.0
    weighted_amount = 0.0
    for item in opportunities:
        if item.amount_min is None and item.amount_max is None:
            amount_basis = 0.0
        elif item.amount_min is None:
            amount_basis = float(item.amount_max or 0)
        elif item.amount_max is None:
            amount_basis = float(item.amount_min or 0)
        else:
            amount_basis = (float(item.amount_min) + float(item.amount_max)) / 2.0
        pipeline_amount += amount_basis
        weighted_amount += float(item.probability_weighted_amount or 0)

    return {
        "pipeline_count": pipeline_count,
        "pipeline_amount": round(pipeline_amount, 2),
        "probability_weighted_amount": round(weighted_amount, 2),
    }


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
        spent = _grant_spent_total(int(grant.id), organization_id)

        if _grant_has_budget_lines(int(grant.id), organization_id):
            remaining = _grant_budget_remaining(int(grant.id), organization_id)
        else:
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
                "amount_disbursed": spent,
                "amount_received": disbursed,
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
