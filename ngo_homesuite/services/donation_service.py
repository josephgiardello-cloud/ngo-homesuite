"""Service layer for donation operations.

All methods are organisation-scoped: callers must supply the org_id for every
write operation so a user from org A can never touch org B's donations.

Status lifecycle:
    pending → received → processed → receipted
    pending → failed          (e.g. Stripe declined / webhook says not paid)
    received → refunded       (manual admin action)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Any, TypeVar, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from ngo_homesuite.models.core import Donation, DonationReceipt, Donor, RecurringDonationPlan, db


_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending":    {"received", "failed"},
    "received":   {"processed", "refunded"},
    "processed":  {"receipted", "refunded"},
    "receipted":  {"refunded"},
    "failed":     {"pending"},   # allow retry
    "refunded":   set(),
}

_VALID_STATUSES = set(_VALID_TRANSITIONS)

_T = TypeVar("_T")


def _model_create(model_cls: type[_T], **kwargs: Any) -> _T:
    """Create SQLAlchemy model instances while keeping strict type-checkers quiet."""
    ctor = cast(Any, model_cls)
    return cast(_T, ctor(**kwargs))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DonationNotFound(Exception):
    pass


class InvalidStatusTransition(Exception):
    pass


class DonationConcurrencyError(RuntimeError):
    """Raised when optimistic concurrency detects stale updates."""


class DonationService:
    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_donation(self, donation_id: int, org_id: int) -> Donation:
        """Fetch a single donation; raises DonationNotFound if missing or wrong org."""
        stmt = select(Donation).where(Donation.id == donation_id, Donation.organization_id == org_id).limit(1)
        donation = db.session.scalars(stmt).first()
        if donation is None:
            raise DonationNotFound(f"Donation {donation_id} not found for org {org_id}")
        return donation

    def list_donations(
        self,
        org_id: int,
        *,
        donor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        fund_id: Optional[int] = None,
        status: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, object]:
        """Return a paginated list of donations for the organisation.

        Returns:
            {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}
        """
        stmt = select(Donation).where(Donation.organization_id == org_id)
        if donor_id is not None:
            stmt = stmt.where(Donation.donor_id == donor_id)
        if project_id is not None:
            stmt = stmt.where(Donation.project_id == project_id)
        if fund_id is not None:
            stmt = stmt.where(Donation.fund_id == fund_id)
        if status is not None:
            stmt = stmt.where(Donation.status == status)

        stmt = stmt.order_by(Donation.donation_date.desc())
        pagination = db.paginate(stmt, page=page, per_page=per_page, error_out=False)
        return {
            "items": pagination.items,
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages,
        }

    def list_filtered_donations(
        self,
        org_id: int,
        *,
        search: Optional[str] = None,
        payment_method: Optional[str] = None,
        status: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
    ) -> list[Donation]:
        stmt = select(Donation).where(Donation.organization_id == org_id)
        if search:
            like_term = f"%{search.strip()}%"
            stmt = stmt.where(
                (Donation.donor_name.ilike(like_term))
                | (Donation.donor_email.ilike(like_term))
                | (Donation.reference_number.ilike(like_term))
                | (Donation.purpose.ilike(like_term))
            )
        if payment_method:
            stmt = stmt.where(Donation.payment_method == payment_method)
        if status:
            stmt = stmt.where(Donation.status == status)
        if min_amount is not None:
            stmt = stmt.where(Donation.amount >= min_amount)
        if max_amount is not None:
            stmt = stmt.where(Donation.amount <= max_amount)
        stmt = stmt.order_by(Donation.donation_date.desc())
        return list(db.session.scalars(stmt))

    def list_recurring_plans(self, org_id: int) -> list[RecurringDonationPlan]:
        stmt = (
            select(RecurringDonationPlan)
            .where(RecurringDonationPlan.organization_id == org_id)
            .order_by(RecurringDonationPlan.created_at.desc())
        )
        return list(db.session.scalars(stmt))

    def create_recurring_plan(
        self,
        org_id: int,
        donor_id: int,
        *,
        amount: float,
        currency: str,
        payment_method: str,
        purpose: Optional[str],
        frequency: str,
        next_charge_date: date,
        status: str = "active",
    ) -> RecurringDonationPlan:
        donor = db.session.scalars(
            select(Donor).where(Donor.id == donor_id, Donor.organization_id == org_id).limit(1)
        ).first()
        if donor is None:
            raise ValueError(f"Donor {donor_id} not found in org {org_id}")
        if amount <= 0:
            raise ValueError("Recurring amount must be positive")

        plan = _model_create(
            RecurringDonationPlan,
            organization_id=org_id,
            donor_id=donor.id,
            amount=float(amount),
            currency=(currency or "USD").upper(),
            payment_method=payment_method,
            purpose=purpose,
            frequency=frequency,
            next_charge_date=next_charge_date,
            status=status,
        )
        db.session.add(plan)
        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise DonationConcurrencyError(
                f"Concurrent update detected while creating recurring plan for donor {donor_id}; please retry."
            ) from exc
        return plan

    def process_due_recurring_plans(
        self,
        org_id: int,
        *,
        run_date: Optional[date] = None,
    ) -> dict[str, int]:
        today = run_date or date.today()
        stmt = (
            select(RecurringDonationPlan)
            .where(
                RecurringDonationPlan.organization_id == org_id,
                RecurringDonationPlan.status == "active",
                RecurringDonationPlan.next_charge_date <= today,
            )
            .order_by(RecurringDonationPlan.id.asc())
        )
        plans = list(db.session.scalars(stmt))

        processed = 0
        failed = 0

        for plan in plans:
            donor = db.session.scalars(
                select(Donor).where(Donor.id == plan.donor_id, Donor.organization_id == org_id).limit(1)
            ).first()
            if donor is None or (plan.payment_method in ("credit_card", "bank_transfer") and not donor.email):
                plan.status = "failed"
                plan.fail_count = int(plan.fail_count or 0) + 1
                plan.last_error = "Missing donor contact info for payment retry workflow."
                plan.updated_at = _utcnow()
                failed += 1
                continue

            donation = _model_create(
                Donation,
                organization_id=org_id,
                donor_id=donor.id,
                donor_name=donor.name,
                donor_email=donor.email,
                donor_phone=donor.phone,
                amount=plan.amount,
                currency=plan.currency,
                payment_method=plan.payment_method,
                purpose=plan.purpose,
                status="received",
                notes=f"Recurring donation charge from plan #{plan.id}",
            )
            db.session.add(donation)
            db.session.flush()

            # Keep recurring charges on the same lifecycle as manual/public donations.
            donation.status = "processed"

            receipt = db.session.scalars(
                select(DonationReceipt).where(DonationReceipt.donation_id == donation.id).limit(1)
            ).first()
            if receipt is None:
                receipt_number = f"RCP-{org_id}-{donation.id}-{uuid.uuid4().hex[:8].upper()}"
                db.session.add(
                    _model_create(
                        DonationReceipt,
                        donation_id=donation.id,
                        receipt_number=receipt_number,
                        status="generated",
                        sent_to_email=donor.email,
                    )
                )
            donation.status = "receipted"

            if plan.frequency == "quarterly":
                plan.next_charge_date = plan.next_charge_date + timedelta(days=90)
            elif plan.frequency == "yearly":
                plan.next_charge_date = plan.next_charge_date + timedelta(days=365)
            else:
                plan.next_charge_date = plan.next_charge_date + timedelta(days=30)
            plan.fail_count = 0
            plan.last_error = None
            plan.status = "active"
            plan.updated_at = _utcnow()
            processed += 1

        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise DonationConcurrencyError(
                f"Concurrent update detected while processing recurring plans for org {org_id}; please retry."
            ) from exc
        return {"processed": processed, "failed": failed}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create_donation(
        self,
        org_id: int,
        donor_name: str,
        amount: float,
        *,
        currency: str = "USD",
        donor_email: Optional[str] = None,
        donor_phone: Optional[str] = None,
        donor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        fund_id: Optional[int] = None,
        payment_method: Optional[str] = None,
        reference_number: Optional[str] = None,
        purpose: Optional[str] = None,
        notes: Optional[str] = None,
        status: str = "received",
        donation_date: Optional[datetime] = None,
        actor_id: Optional[int] = None,
    ) -> Donation:
        """Create and persist a new donation record.

        Raises:
            ValueError: on invalid inputs (negative amount, bad currency, bad status).
            IntegrityError (re-raised): if reference_number already exists.
        """
        if amount <= 0:
            raise ValueError(f"Donation amount must be positive, got {amount}")
        if not donor_name or not donor_name.strip():
            raise ValueError("donor_name is required")
        currency = currency.upper()
        if len(currency) != 3:
            raise ValueError(f"currency must be an ISO-4217 3-letter code, got {currency!r}")
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid initial status {status!r}. Valid: {sorted(_VALID_STATUSES)}")

        # If donor_id supplied, validate it belongs to this org
        if donor_id is not None:
            donor = db.session.scalars(
                select(Donor).where(Donor.id == donor_id, Donor.organization_id == org_id).limit(1)
            ).first()
            if donor is None:
                raise ValueError(f"Donor {donor_id} not found in org {org_id}")

        donation = _model_create(
            Donation,
            organization_id=org_id,
            donor_name=donor_name.strip(),
            amount=float(amount),
            currency=currency,
            donor_email=donor_email,
            donor_phone=donor_phone,
            donor_id=donor_id,
            project_id=project_id,
            fund_id=fund_id,
            payment_method=payment_method,
            reference_number=reference_number,
            purpose=purpose,
            notes=notes,
            status=status,
            donation_date=donation_date or _utcnow(),
        )
        db.session.add(donation)
        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise DonationConcurrencyError(
                f"Concurrent update detected while creating donation for org {org_id}; please retry."
            ) from exc
        except IntegrityError:
            db.session.rollback()
            raise

        return donation

    def update_donation(
        self,
        donation_id: int,
        org_id: int,
        actor_id: Optional[int] = None,
        **fields: object,
    ) -> Donation:
        """Update mutable fields on a donation (not status — use update_status for that).

        Allowed fields: donor_name, donor_email, donor_phone, purpose, notes,
                        payment_method, reference_number, project_id, fund_id.
        Raises DonationNotFound if not found or wrong org.
        """
        donation = self.get_donation(donation_id, org_id)

        mutable = {
            "donor_name", "donor_email", "donor_phone",
            "purpose", "notes", "payment_method", "reference_number",
            "project_id", "fund_id",
        }
        for key, value in fields.items():
            if key not in mutable:
                raise ValueError(f"Field {key!r} is not updatable via this method")
            setattr(donation, key, value)

        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise DonationConcurrencyError(
                f"Concurrent update detected for donation {donation_id}; please reload and retry."
            ) from exc
        return donation

    def update_status(
        self,
        donation_id: int,
        org_id: int,
        new_status: str,
        actor_id: Optional[int] = None,
    ) -> Donation:
        """Transition a donation's status along valid lifecycle paths.

        Raises:
            DonationNotFound: if not found or wrong org.
            InvalidStatusTransition: if the transition is not allowed.
        """
        donation = self.get_donation(donation_id, org_id)
        allowed = _VALID_TRANSITIONS.get(donation.status, set())
        if new_status not in allowed:
            raise InvalidStatusTransition(
                f"Cannot transition donation {donation_id} from {donation.status!r} to {new_status!r}. "
                f"Allowed next states: {sorted(allowed) or 'none (terminal)'}"
            )
        donation.status = new_status
        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise DonationConcurrencyError(
                f"Concurrent update detected for donation {donation_id}; please reload and retry."
            ) from exc
        return donation

    def delete_donation(
        self,
        donation_id: int,
        org_id: int,
        actor_id: Optional[int] = None,
    ) -> None:
        """Hard-delete a donation record.

        Only permitted for donations in 'pending' or 'failed' status; use
        update_status('refunded') for money-in-transit donations.
        Raises DonationNotFound or ValueError for non-deletable statuses.
        """
        donation = self.get_donation(donation_id, org_id)
        if donation.status not in ("pending", "failed"):
            raise ValueError(
                f"Cannot delete donation {donation_id} with status {donation.status!r}. "
                "Only pending/failed donations may be deleted; mark others as refunded."
            )
        db.session.delete(donation)
        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise DonationConcurrencyError(
                f"Concurrent update detected for donation {donation_id}; please reload and retry."
            ) from exc

    # ------------------------------------------------------------------
    # Receipt
    # ------------------------------------------------------------------

    def generate_receipt(
        self,
        donation_id: int,
        org_id: int,
        sent_to_email: Optional[str] = None,
    ) -> DonationReceipt:
        """Create (or return existing) a receipt for a donation.

        Receipt number format: RCP-<org_id>-<donation_id>-<8-char uuid suffix>
        The donation status is advanced to 'receipted' if currently 'processed'.
        """
        donation = self.get_donation(donation_id, org_id)
        if donation.status not in ("processed", "receipted"):
            raise ValueError(
                f"Cannot generate receipt for donation with status {donation.status!r}. "
                "Donation must be in 'processed' state."
            )

        # Return existing receipt if already generated
        existing = db.session.scalars(
            select(DonationReceipt).where(DonationReceipt.donation_id == donation_id).limit(1)
        ).first()
        if existing:
            return existing

        receipt_number = f"RCP-{org_id}-{donation_id}-{uuid.uuid4().hex[:8].upper()}"
        receipt = _model_create(
            DonationReceipt,
            donation_id=donation_id,
            receipt_number=receipt_number,
            status="generated",
            sent_to_email=sent_to_email or donation.donor_email,
        )
        db.session.add(receipt)

        if donation.status == "processed":
            donation.status = "receipted"

        try:
            db.session.commit()
        except StaleDataError as exc:
            db.session.rollback()
            raise DonationConcurrencyError(
                f"Concurrent update detected for donation {donation_id} during receipt generation; please reload and retry."
            ) from exc
        return receipt
