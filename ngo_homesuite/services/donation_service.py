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
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from ngo_homesuite.models.core import Donation, DonationReceipt, Donor, db


_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending":    {"received", "failed"},
    "received":   {"processed", "refunded"},
    "processed":  {"receipted", "refunded"},
    "receipted":  {"refunded"},
    "failed":     {"pending"},   # allow retry
    "refunded":   set(),
}

_VALID_STATUSES = set(_VALID_TRANSITIONS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DonationNotFound(Exception):
    pass


class InvalidStatusTransition(Exception):
    pass


class DonationService:
    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_donation(self, donation_id: int, org_id: int) -> Donation:
        """Fetch a single donation; raises DonationNotFound if missing or wrong org."""
        donation = Donation.query.filter_by(id=donation_id, organization_id=org_id).first()
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
    ) -> dict:
        """Return a paginated list of donations for the organisation.

        Returns:
            {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}
        """
        query = Donation.query.filter_by(organization_id=org_id)
        if donor_id is not None:
            query = query.filter_by(donor_id=donor_id)
        if project_id is not None:
            query = query.filter_by(project_id=project_id)
        if fund_id is not None:
            query = query.filter_by(fund_id=fund_id)
        if status is not None:
            query = query.filter_by(status=status)

        pagination = query.order_by(Donation.donation_date.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        return {
            "items": pagination.items,
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages,
        }

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
            donor = Donor.query.filter_by(id=donor_id, organization_id=org_id).first()
            if donor is None:
                raise ValueError(f"Donor {donor_id} not found in org {org_id}")

        donation = Donation(
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
            raise RuntimeError(
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
        **fields,
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
            raise RuntimeError(
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
            raise RuntimeError(
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
            raise RuntimeError(
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
        existing = DonationReceipt.query.filter_by(donation_id=donation_id).first()
        if existing:
            return existing

        receipt_number = f"RCP-{org_id}-{donation_id}-{uuid.uuid4().hex[:8].upper()}"
        receipt = DonationReceipt(
            donation_id=donation_id,
            receipt_number=receipt_number,
            status="generated",
            sent_to_email=sent_to_email or donation.donor_email,
        )
        db.session.add(receipt)

        if donation.status == "processed":
            donation.status = "receipted"

        db.session.commit()
        return receipt
