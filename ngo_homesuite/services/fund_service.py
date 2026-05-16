"""Service layer for Fund operations.

Funds are allocation buckets for donations and expenses.  Deactivating
(is_active=False) is the soft-delete pattern; hard deletes are blocked
when a fund has associated donations or expenses.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, or_, select

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from ngo_homesuite.models.core import Donation, Expense, Fund, db


class FundNotFound(Exception):
    pass


class FundHasTransactions(Exception):
    """Raised when attempting to delete a fund that still has linked records."""


class FundService:
    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_fund(self, fund_id: int, org_id: int) -> Fund:
        """Fetch a single fund; raises FundNotFound if missing or wrong org."""
        stmt = select(Fund).where(Fund.id == fund_id, Fund.organization_id == org_id).limit(1)
        fund = db.session.scalars(stmt).first()
        if fund is None:
            raise FundNotFound(f"Fund {fund_id} not found for org {org_id}")
        return fund

    def list_funds(
        self,
        org_id: int,
        *,
        active_only: bool = False,
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        """Return a paginated list of funds for the organisation.

        Returns:
            {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}
        """
        stmt = select(Fund).where(Fund.organization_id == org_id)
        if active_only:
            stmt = stmt.where(Fund.is_active == True)
        stmt = stmt.order_by(Fund.name.asc())
        pagination = db.paginate(stmt, page=page, per_page=per_page, error_out=False)
        return {
            "items": pagination.items,
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages,
        }

    def get_fund_balance(self, fund_id: int, org_id: int) -> dict:
        """Return total donations in, total expenses out, and net balance for a fund.

        All amounts are in their stored currency (mixed-currency funds show the
        raw sums — callers should aggregate by currency if FX matters).
        """
        self.get_fund(fund_id, org_id)  # raises FundNotFound if wrong org

        total_in = db.session.scalar(
            select(func.coalesce(func.sum(Donation.amount), 0.0)).where(
                Donation.fund_id == fund_id,
                Donation.organization_id == org_id,
            )
        ) or 0.0
        total_out = db.session.scalar(
            select(func.coalesce(func.sum(Expense.amount), 0.0)).where(
                Expense.fund_id == fund_id,
                Expense.organization_id == org_id,
            )
        ) or 0.0
        return {
            "fund_id": fund_id,
            "total_in": round(float(total_in), 2),
            "total_out": round(float(total_out), 2),
            "net_balance": round(float(total_in) - float(total_out), 2),
        }

    def list_all_funds(
        self,
        org_id: int,
        *,
        search: Optional[str] = None,
        status: Optional[str] = None,
        active_only: bool = False,
    ) -> list[Fund]:
        stmt = select(Fund).where(Fund.organization_id == org_id)
        if active_only:
            stmt = stmt.where(Fund.is_active == True)
        if status == "active":
            stmt = stmt.where(Fund.is_active == True)
        elif status == "inactive":
            stmt = stmt.where(Fund.is_active == False)
        if search:
            like_term = f"%{search.strip()}%"
            stmt = stmt.where(or_(Fund.name.ilike(like_term), Fund.description.ilike(like_term)))
        stmt = stmt.order_by(Fund.name.asc(), Fund.id.asc())
        return list(db.session.scalars(stmt))

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create_fund(
        self,
        org_id: int,
        name: str,
        *,
        description: Optional[str] = None,
        is_active: bool = True,
        actor_id: Optional[int] = None,
    ) -> Fund:
        """Create and persist a new fund record.

        Raises:
            ValueError: if name is blank.
            IntegrityError (re-raised): if name is duplicate within the org.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Fund name is required")

        fund = Fund(
            organization_id=org_id,
            name=name,
            description=(description or "").strip() or None,
            is_active=is_active,
        )
        db.session.add(fund)
        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise RuntimeError(
                f"Concurrent update detected while creating fund for org {org_id}; please retry."
            ) from exc
        except IntegrityError as exc:
            db.session.rollback()
            raise ValueError(f"Fund name '{name}' already exists for this organization") from exc
        return fund

    def update_fund(
        self,
        fund_id: int,
        org_id: int,
        actor_id: Optional[int] = None,
        **fields,
    ) -> Fund:
        """Update mutable fields on a fund.

        Allowed fields: name, description, is_active.
        """
        fund = self.get_fund(fund_id, org_id)
        mutable = {"name", "description", "is_active"}
        for key, value in fields.items():
            if key not in mutable:
                raise ValueError(f"Field {key!r} is not updatable via this method")
            if key == "name":
                value = (value or "").strip()
                if not value:
                    raise ValueError("Fund name cannot be blank")
            if key == "description":
                value = (value or "").strip() or None
            setattr(fund, key, value)
        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise RuntimeError(
                f"Concurrent update detected for fund {fund_id}; please reload and retry."
            ) from exc
        except IntegrityError as exc:
            db.session.rollback()
            raise ValueError(f"Fund name '{fields.get('name')}' already exists for this organization") from exc
        return fund

    def delete_fund(
        self, fund_id: int, org_id: int, actor_id: Optional[int] = None
    ) -> None:
        """Hard-delete a fund only when it has no associated donations or expenses.

        Raises:
            FundNotFound: if the fund does not exist in this org.
            FundHasTransactions: if the fund has linked donations or expenses.
        """
        fund = self.get_fund(fund_id, org_id)

        donation_count = db.session.scalar(
            select(func.count(Donation.id)).where(
                Donation.fund_id == fund_id,
                Donation.organization_id == org_id,
            )
        )
        expense_count = db.session.scalar(
            select(func.count(Expense.id)).where(
                Expense.fund_id == fund_id,
                Expense.organization_id == org_id,
            )
        )
        if donation_count or expense_count:
            raise FundHasTransactions(
                f"Fund {fund_id} has {donation_count} donation(s) and "
                f"{expense_count} expense(s). Deactivate it instead of deleting."
            )

        db.session.delete(fund)
        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise RuntimeError(
                f"Concurrent update detected for fund {fund_id}; please reload and retry."
            ) from exc

    def deactivate_fund(
        self, fund_id: int, org_id: int, actor_id: Optional[int] = None
    ) -> Fund:
        """Soft-delete: set is_active=False without destroying transaction history."""
        return self.update_fund(fund_id, org_id, actor_id=actor_id, is_active=False)
