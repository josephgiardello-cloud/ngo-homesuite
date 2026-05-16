"""Service layer for donor operations.

All write operations are organisation-scoped.  Donors are treated as
append-only / soft-deactivated records — hard deletes are not permitted
because donation history depends on donor records remaining intact.
"""
from __future__ import annotations

from typing import Optional

from ngo_homesuite.models.core import Donor, db


_VALID_DONOR_TYPES = {"individual", "corporate", "foundation", "anonymous"}


class DonorNotFound(Exception):
    pass


class DonorService:
    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_donor(self, donor_id: int, org_id: int) -> Donor:
        """Fetch a single donor; raises DonorNotFound if missing or wrong org."""
        donor = Donor.query.filter_by(id=donor_id, organization_id=org_id).first()
        if donor is None:
            raise DonorNotFound(f"Donor {donor_id} not found for org {org_id}")
        return donor

    def find_by_email(self, org_id: int, email: str) -> Optional[Donor]:
        """Return the first donor in this org matching the email, or None."""
        if not email or not email.strip():
            return None
        return Donor.query.filter_by(
            organization_id=org_id, email=email.strip().lower()
        ).first()

    def list_donors(
        self,
        org_id: int,
        *,
        donor_type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        """Return a paginated list of donors for the organisation.

        Returns:
            {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}
        """
        query = Donor.query.filter_by(organization_id=org_id)
        if donor_type:
            query = query.filter_by(donor_type=donor_type)
        if search:
            term = f"%{search.strip()}%"
            query = query.filter(
                db.or_(Donor.name.ilike(term), Donor.email.ilike(term))
            )
        pagination = query.order_by(Donor.name.asc()).paginate(
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

    def create_donor(
        self,
        org_id: int,
        name: str,
        *,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        donor_type: str = "individual",
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> Donor:
        """Create and persist a new donor record.

        Raises:
            ValueError: on invalid inputs.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Donor name is required")
        if donor_type not in _VALID_DONOR_TYPES:
            raise ValueError(
                f"Invalid donor_type {donor_type!r}. Valid: {sorted(_VALID_DONOR_TYPES)}"
            )
        email_clean = email.strip().lower() if email and email.strip() else None

        donor = Donor(
            organization_id=org_id,
            name=name,
            email=email_clean,
            phone=(phone or "").strip() or None,
            donor_type=donor_type,
            notes=notes,
        )
        db.session.add(donor)
        db.session.commit()
        return donor

    def find_or_create_by_email(
        self,
        org_id: int,
        email: Optional[str],
        name: str,
        *,
        phone: Optional[str] = None,
        donor_type: str = "individual",
        notes: Optional[str] = None,
    ) -> tuple[Donor, bool]:
        """Return (donor, created) — find by email if available, else create.

        When ``email`` is None/blank a new donor is always created (anonymous
        or cash donors without email are legitimate).

        This is intentionally NOT wrapped in its own transaction so the caller
        can include subsequent inserts (e.g. Donation) in the same atomic
        session.commit().
        """
        if email and email.strip():
            existing = self.find_by_email(org_id, email)
            if existing:
                return existing, False

        email_clean = email.strip().lower() if email and email.strip() else None
        donor = Donor(
            organization_id=org_id,
            name=(name or "").strip() or "Anonymous",
            email=email_clean,
            phone=(phone or "").strip() or None,
            donor_type=donor_type,
            notes=notes,
        )
        db.session.add(donor)
        db.session.flush()  # get PK without committing — caller commits
        return donor, True

    def update_donor(
        self,
        donor_id: int,
        org_id: int,
        actor_id: Optional[int] = None,
        **fields,
    ) -> Donor:
        """Update mutable fields on a donor.

        Allowed fields: name, email, phone, donor_type, notes.
        Email is normalised to lower-case.
        """
        donor = self.get_donor(donor_id, org_id)
        mutable = {"name", "email", "phone", "donor_type", "notes"}
        for key, value in fields.items():
            if key not in mutable:
                raise ValueError(f"Field {key!r} is not updatable via this method")
            if key == "email" and value:
                value = value.strip().lower()
            if key == "donor_type" and value not in _VALID_DONOR_TYPES:
                raise ValueError(
                    f"Invalid donor_type {value!r}. Valid: {sorted(_VALID_DONOR_TYPES)}"
                )
            setattr(donor, key, value)
        db.session.commit()
        return donor

    def delete_donor(self, donor_id: int, org_id: int, actor_id: Optional[int] = None) -> None:
        """Soft-delete is the right pattern — hard deletes break donation history.

        This method intentionally raises; callers should instead set a
        ``is_active=False`` flag if soft-delete is added to the Donor model,
        or simply leave inactive donors in place.
        """
        raise NotImplementedError(
            "Hard-deleting donors is not permitted because donation history references "
            "donor records.  Mark the donor inactive instead."
        )
