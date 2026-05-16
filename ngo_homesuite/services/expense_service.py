"""Service layer for expense operations."""

from __future__ import annotations

from typing import Optional
from sqlalchemy import select

from ngo_homesuite.models.core import Expense, db


class ExpenseService:
    def list_filtered_expenses(
        self,
        org_id: int,
        *,
        search: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
    ) -> list[Expense]:
        stmt = select(Expense).where(Expense.organization_id == org_id)
        if search:
            like_term = f"%{search.strip()}%"
            stmt = stmt.where((Expense.payee.ilike(like_term)) | (Expense.description.ilike(like_term)))
        if min_amount is not None:
            stmt = stmt.where(Expense.amount >= min_amount)
        if max_amount is not None:
            stmt = stmt.where(Expense.amount <= max_amount)
        stmt = stmt.order_by(Expense.paid_at.desc())
        return list(db.session.scalars(stmt))

    def create_expense(
        self,
        org_id: int,
        *,
        project_id: Optional[int],
        fund_id: Optional[int],
        amount: float,
        currency: str,
        payee: Optional[str],
        description: Optional[str],
    ) -> Expense:
        if amount <= 0:
            raise ValueError("Expense amount must be positive")

        expense = Expense(
            organization_id=org_id,
            project_id=project_id,
            fund_id=fund_id,
            amount=float(amount),
            currency=(currency or "USD").upper(),
            payee=(payee or "").strip() or None,
            description=(description or "").strip() or None,
        )
        db.session.add(expense)
        db.session.commit()
        return expense
