"""Service layer for Fund operations.

Funds are allocation buckets for donations and expenses.  Deactivating
(is_active=False) is the soft-delete pattern; hard deletes are blocked
when a fund has associated donations or expenses.
"""
from __future__ import annotations

from typing import Optional
from decimal import Decimal

from sqlalchemy import func, or_, select

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from ngo_homesuite.models.core import Donation, Expense, Fund, db
from ngo_homesuite.services.donation_service import COLLECTED_DONATION_STATUSES


class FundNotFound(Exception):
    pass


class FundHasTransactions(Exception):
    """Raised when attempting to delete a fund that still has linked records."""


class FundConcurrencyError(RuntimeError):
    """Raised when optimistic concurrency detects stale updates."""


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
        """Return separate original-currency balances, never an implicit FX total.

        Legacy scalar totals are available only for a zero/single-currency fund;
        they are None for mixed currencies. No exchange rates are inferred.
        """
        self.get_fund(fund_id, org_id)  # raises FundNotFound if wrong org

        donations = db.session.execute(
            select(Donation.currency, Donation.amount).where(
                Donation.fund_id == fund_id,
                Donation.organization_id == org_id,
                Donation.status.in_(COLLECTED_DONATION_STATUSES),
            )
        ).all()
        expenses = db.session.execute(
            select(Expense.currency, Expense.amount).where(
                Expense.fund_id == fund_id,
                Expense.organization_id == org_id,
            )
        ).all()
        totals: dict[str, dict[str, Decimal]] = {}
        for rows, field in ((donations, "total_in"), (expenses, "total_out")):
            for currency, amount in rows:
                values = totals.setdefault(currency, {"total_in": Decimal(0), "total_out": Decimal(0)})
                values[field] += Decimal(str(amount))
        balances = {
            currency: {
                "total_in": float(values["total_in"].quantize(Decimal("0.01"))),
                "total_out": float(values["total_out"].quantize(Decimal("0.01"))),
                "net_balance": float((values["total_in"] - values["total_out"]).quantize(Decimal("0.01"))),
            }
            for currency, values in sorted(totals.items())
        }
        single = next(iter(balances.values())) if len(balances) == 1 else None
        return {
            "fund_id": fund_id,
            "currency": next(iter(balances)) if len(balances) == 1 else None,
            "balances_by_currency": balances,
            "total_in": single["total_in"] if single else (None if balances else 0.0),
            "total_out": single["total_out"] if single else (None if balances else 0.0),
            "net_balance": single["net_balance"] if single else (None if balances else 0.0),
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
            raise FundConcurrencyError(
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
            raise FundConcurrencyError(
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
            raise FundConcurrencyError(
                f"Concurrent update detected for fund {fund_id}; please reload and retry."
            ) from exc

    def deactivate_fund(
        self, fund_id: int, org_id: int, actor_id: Optional[int] = None
    ) -> Fund:
        """Soft-delete: set is_active=False without destroying transaction history."""
        return self.update_fund(fund_id, org_id, actor_id=actor_id, is_active=False)
