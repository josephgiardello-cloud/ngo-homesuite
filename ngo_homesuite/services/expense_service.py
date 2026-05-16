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
        grant_id: Optional[int] = None,
        expense_category: Optional[str] = None,
        supporting_document_ref: Optional[str] = None,
    ) -> Expense:
        if amount <= 0:
            raise ValueError("Expense amount must be positive")
        clean_currency = (currency or "USD").strip().upper()
        if len(clean_currency) != 3:
            raise ValueError("currency must be a 3-letter ISO code")

        expense = Expense(
            organization_id=org_id,
            project_id=project_id,
            fund_id=fund_id,
            amount=float(amount),
            currency=clean_currency,
            payee=(payee or "").strip() or None,
            description=(description or "").strip() or None,
        )
        db.session.add(expense)
        db.session.flush()

        if grant_id is not None:
            if not (expense_category or "").strip():
                db.session.rollback()
                raise ValueError("expense_category is required when grant_id is provided")
            from ngo_homesuite.services import grant_service

            try:
                grant_service.allocate_expense_to_budget_line(
                    grant_id=grant_id,
                    organization_id=org_id,
                    expense_id=int(expense.id),
                    category=expense_category,
                    supporting_document_ref=supporting_document_ref,
                    commit=False,
                )
            except Exception:
                db.session.rollback()
                raise

        db.session.commit()
        return expense
