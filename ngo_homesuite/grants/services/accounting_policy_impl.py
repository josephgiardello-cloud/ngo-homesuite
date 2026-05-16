"""Grant accounting policy primitives for carry-forward and allowable-cost checks."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from sqlalchemy import func

from ngo_homesuite.models.core import Expense, GrantDisbursement, GrantExpenseAllocation, db


class GrantAccountingPolicyError(ValueError):
    """Raised when an allocation violates accounting policy."""


_UNALLOWABLE_KEYWORDS = {
    "alcohol",
    "lobbying",
    "fine",
    "penalty",
    "gift card",
}

_INDIRECT_EXCLUDED_CATEGORIES = {
    "indirect",
    "admin_overhead",
    "unallowable",
}


def evaluate_allowable_cost(
    category: str,
    *,
    description: Optional[str] = None,
    payee: Optional[str] = None,
) -> dict:
    normalized_category = (category or "").strip().lower()
    haystack = " ".join(filter(None, [(description or "").lower(), (payee or "").lower(), normalized_category]))
    hits = sorted(keyword for keyword in _UNALLOWABLE_KEYWORDS if keyword in haystack)
    return {
        "allowed": len(hits) == 0,
        "category": normalized_category,
        "matched_keywords": hits,
    }


def enforce_allowable_cost(
    category: str,
    *,
    description: Optional[str] = None,
    payee: Optional[str] = None,
) -> None:
    result = evaluate_allowable_cost(category, description=description, payee=payee)
    if result["allowed"]:
        return
    raise GrantAccountingPolicyError(
        "allocation blocked by unallowable cost policy: " + ", ".join(result["matched_keywords"])
    )


def compute_multi_year_carry_forward(grant_id: int, organization_id: int) -> list[dict]:
    disbursements = list(
        db.session.query(
            func.strftime("%Y", GrantDisbursement.received_date).label("year"),
            func.coalesce(func.sum(GrantDisbursement.amount), 0).label("amount"),
        )
        .filter(
            GrantDisbursement.grant_id == grant_id,
            GrantDisbursement.organization_id == organization_id,
        )
        .group_by(func.strftime("%Y", GrantDisbursement.received_date))
        .all()
    )

    spending = list(
        db.session.query(
            func.strftime("%Y", Expense.paid_at).label("year"),
            func.coalesce(func.sum(GrantExpenseAllocation.amount), 0).label("amount"),
        )
        .select_from(GrantExpenseAllocation)
        .join(Expense, Expense.id == GrantExpenseAllocation.expense_id)
        .filter(
            GrantExpenseAllocation.grant_id == grant_id,
            GrantExpenseAllocation.organization_id == organization_id,
        )
        .group_by(func.strftime("%Y", Expense.paid_at))
        .all()
    )

    disbursed_by_year = {int(year): float(amount or 0) for year, amount in disbursements if year is not None}
    spent_by_year = {int(year): float(amount or 0) for year, amount in spending if year is not None}

    years = sorted(set(disbursed_by_year) | set(spent_by_year))
    carry_forward_running = 0.0
    summary: list[dict] = []
    for year in years:
        disbursed = disbursed_by_year.get(year, 0.0)
        spent = spent_by_year.get(year, 0.0)
        carry_forward_running = carry_forward_running + disbursed - spent
        summary.append(
            {
                "year": year,
                "disbursed": disbursed,
                "spent": spent,
                "carry_forward": max(0.0, carry_forward_running),
            }
        )
    return summary


def compute_indirect_cost_pool(
    grant_id: int,
    organization_id: int,
    *,
    indirect_rate: float,
) -> dict:
    rate = float(indirect_rate)
    if rate < 0 or rate > 1:
        raise GrantAccountingPolicyError("indirect_rate must be between 0 and 1")

    rows = list(
        db.session.query(
            GrantExpenseAllocation.category,
            func.coalesce(func.sum(GrantExpenseAllocation.amount), 0),
        )
        .filter(
            GrantExpenseAllocation.grant_id == grant_id,
            GrantExpenseAllocation.organization_id == organization_id,
        )
        .group_by(GrantExpenseAllocation.category)
        .all()
    )

    direct_base = 0.0
    category_totals = defaultdict(float)
    for category, amount in rows:
        normalized_category = (category or "").strip().lower()
        value = float(amount or 0)
        category_totals[normalized_category] += value
        if normalized_category not in _INDIRECT_EXCLUDED_CATEGORIES:
            direct_base += value

    return {
        "indirect_rate": rate,
        "direct_cost_base": direct_base,
        "calculated_indirect_pool": round(direct_base * rate, 2),
        "category_totals": dict(category_totals),
    }
