"""Service layer for donor operations.

All write operations are organisation-scoped.  Donors are treated as
append-only / soft-deactivated records — hard deletes are not permitted
because donation history depends on donor records remaining intact.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, or_, select

from ngo_homesuite.models.core import Donation, Donor, db


_VALID_DONOR_TYPES = {"individual", "corporate", "foundation", "anonymous"}


class DonorNotFound(Exception):
    pass


class DonorService:
    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_donor(self, donor_id: int, org_id: int) -> Donor:
        """Fetch a single donor; raises DonorNotFound if missing or wrong org."""
        stmt = select(Donor).where(Donor.id == donor_id, Donor.organization_id == org_id).limit(1)
        donor = db.session.scalars(stmt).first()
        if donor is None:
            raise DonorNotFound(f"Donor {donor_id} not found for org {org_id}")
        return donor

    def find_by_email(self, org_id: int, email: str) -> Optional[Donor]:
        """Return the first donor in this org matching the email, or None."""
        if not email or not email.strip():
            return None
        stmt = select(Donor).where(
            Donor.organization_id == org_id,
            Donor.email == email.strip().lower(),
        ).limit(1)
        return db.session.scalars(stmt).first()

    def list_donors(
        self,
        org_id: int,
        *,
        donor_type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, object]:
        """Return a paginated list of donors for the organisation.

        Returns:
            {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}
        """
        stmt = select(Donor).where(Donor.organization_id == org_id)
        if donor_type:
            stmt = stmt.where(Donor.donor_type == donor_type)
        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Donor.name.ilike(term),
                    Donor.preferred_name.ilike(term),
                    Donor.email.ilike(term),
                    Donor.phone.ilike(term),
                    Donor.address.ilike(term),
                    Donor.city.ilike(term),
                    Donor.employer.ilike(term),
                    Donor.source.ilike(term),
                )
            )
        stmt = stmt.order_by(Donor.name.asc())
        pagination = db.paginate(stmt, page=page, per_page=per_page, error_out=False)
        return {
            "items": pagination.items,
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages,
        }

    def list_all_donors(
        self,
        org_id: int,
        *,
        donor_type: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[Donor]:
        stmt = select(Donor).where(Donor.organization_id == org_id)
        if donor_type:
            stmt = stmt.where(Donor.donor_type == donor_type)
        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Donor.name.ilike(term),
                    Donor.preferred_name.ilike(term),
                    Donor.email.ilike(term),
                    Donor.phone.ilike(term),
                    Donor.address.ilike(term),
                    Donor.city.ilike(term),
                    Donor.employer.ilike(term),
                    Donor.source.ilike(term),
                )
            )
        stmt = stmt.order_by(Donor.name.asc(), Donor.id.asc())
        return list(db.session.scalars(stmt))

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
        salutation: Optional[str] = None,
        preferred_name: Optional[str] = None,
        status: str = "active",
        address: Optional[str] = None,
        city: Optional[str] = None,
        country: Optional[str] = None,
        postal_code: Optional[str] = None,
        preferred_contact_method: str = "email",
        communication_opt_in: bool = True,
        employer: Optional[str] = None,
        source: Optional[str] = None,
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
        allowed_statuses = {"active", "prospect", "lapsed", "archived"}
        if status not in allowed_statuses:
            raise ValueError(f"Invalid status {status!r}. Valid: {sorted(allowed_statuses)}")
        allowed_contact_methods = {"email", "phone", "mail", "none"}
        if preferred_contact_method not in allowed_contact_methods:
            raise ValueError(
                f"Invalid preferred_contact_method {preferred_contact_method!r}. Valid: {sorted(allowed_contact_methods)}"
            )
        email_clean = email.strip().lower() if email and email.strip() else None

        donor = Donor(
            organization_id=org_id,
            name=name,
            email=email_clean,
            phone=(phone or "").strip() or None,
            donor_type=donor_type,
            salutation=(salutation or "").strip() or None,
            preferred_name=(preferred_name or "").strip() or None,
            status=status,
            address=(address or "").strip() or None,
            city=(city or "").strip() or None,
            country=(country or "").strip() or None,
            postal_code=(postal_code or "").strip() or None,
            preferred_contact_method=preferred_contact_method,
            communication_opt_in=bool(communication_opt_in),
            employer=(employer or "").strip() or None,
            source=(source or "").strip() or None,
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
        salutation: Optional[str] = None,
        preferred_name: Optional[str] = None,
        status: str = "active",
        address: Optional[str] = None,
        city: Optional[str] = None,
        country: Optional[str] = None,
        postal_code: Optional[str] = None,
        preferred_contact_method: str = "email",
        communication_opt_in: bool = True,
        employer: Optional[str] = None,
        source: Optional[str] = None,
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
            salutation=(salutation or "").strip() or None,
            preferred_name=(preferred_name or "").strip() or None,
            status=status,
            address=(address or "").strip() or None,
            city=(city or "").strip() or None,
            country=(country or "").strip() or None,
            postal_code=(postal_code or "").strip() or None,
            preferred_contact_method=preferred_contact_method,
            communication_opt_in=bool(communication_opt_in),
            employer=(employer or "").strip() or None,
            source=(source or "").strip() or None,
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

        Allowed fields: name, email, phone, donor_type, status, salutation, preferred_name, address, city, country, postal_code, preferred_contact_method, communication_opt_in, employer, source, notes.
        Email is normalised to lower-case.
        """
        donor = self.get_donor(donor_id, org_id)
        mutable = {
            "name",
            "email",
            "phone",
            "salutation",
            "preferred_name",
            "donor_type",
            "status",
            "address",
            "city",
            "country",
            "postal_code",
            "preferred_contact_method",
            "communication_opt_in",
            "employer",
            "source",
            "notes",
        }
        for key, value in fields.items():
            if key not in mutable:
                raise ValueError(f"Field {key!r} is not updatable via this method")
            if key == "email" and value:
                value = value.strip().lower()
            if key == "donor_type" and value not in _VALID_DONOR_TYPES:
                raise ValueError(
                    f"Invalid donor_type {value!r}. Valid: {sorted(_VALID_DONOR_TYPES)}"
                )
            if key == "status" and value not in {"active", "prospect", "lapsed", "archived"}:
                raise ValueError("Invalid status value")
            if key == "preferred_contact_method" and value not in {"email", "phone", "mail", "none"}:
                raise ValueError("Invalid preferred_contact_method value")
            if key == "communication_opt_in" and value is not None:
                value = bool(value)
            if key in {"salutation", "preferred_name", "address", "city", "country", "postal_code", "employer", "source"} and value is not None:
                value = str(value).strip() or None
            setattr(donor, key, value)
        db.session.commit()
        return donor

    def delete_donor(self, donor_id: int, org_id: int, actor_id: Optional[int] = None) -> None:
        donor = self.get_donor(donor_id, org_id)
        donation_count = db.session.scalar(
            select(func.count(Donation.id)).where(
                Donation.donor_id == donor.id,
                Donation.organization_id == org_id,
            )
        ) or 0
        if donation_count > 0:
            raise ValueError(
                f"Cannot delete donor {donor_id} with existing donations. Edit donor instead."
            )
        db.session.delete(donor)
        db.session.commit()

    def merge_donors(self, org_id: int, primary_id: int, duplicate_id: int) -> tuple[Donor, Donor]:
        if primary_id == duplicate_id:
            raise ValueError("primary_id and duplicate_id must be different")

        primary = self.get_donor(primary_id, org_id)
        duplicate = self.get_donor(duplicate_id, org_id)

        dup_donations = list(
            db.session.scalars(
                select(Donation).where(
                    Donation.organization_id == org_id,
                    Donation.donor_id == duplicate.id,
                )
            )
        )
        for donation in dup_donations:
            donation.donor_id = primary.id
            donation.donor_name = primary.name
            donation.donor_email = primary.email
            donation.donor_phone = primary.phone

        # Persist FK rewrites before deleting the duplicate donor record.
        db.session.flush()

        if duplicate.notes and duplicate.notes not in (primary.notes or ""):
            primary.notes = ((primary.notes or "").strip() + "\n" + f"[Merged from donor #{duplicate.id}] {duplicate.notes}").strip()

        db.session.delete(duplicate)
        db.session.commit()
        return primary, duplicate
