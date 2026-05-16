"""Membership management service — tiers, records, auto-renewal tracking."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func, select
from werkzeug.exceptions import NotFound

from ngo_homesuite.models.core import MembershipRecord, MembershipTier, db


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

def create_tier(
    organization_id: int,
    name: str,
    price: float,
    *,
    currency: str = "USD",
    interval: str = "annual",
    benefits: Optional[str] = None,
) -> MembershipTier:
    tier = MembershipTier(
        organization_id=organization_id,
        name=name,
        price=price,
        currency=currency,
        interval=interval,
        benefits=benefits,
    )
    db.session.add(tier)
    db.session.commit()
    return tier


def list_tiers(organization_id: int, active_only: bool = True) -> List[MembershipTier]:
    stmt = select(MembershipTier).where(MembershipTier.organization_id == organization_id)
    if active_only:
        stmt = stmt.where(MembershipTier.is_active == True)
    stmt = stmt.order_by(MembershipTier.price.asc())
    return list(db.session.scalars(stmt))


def get_tier(tier_id: int, organization_id: int) -> Optional[MembershipTier]:
    return db.session.scalars(
        select(MembershipTier).where(
            MembershipTier.id == tier_id,
            MembershipTier.organization_id == organization_id,
        ).limit(1)
    ).first()


def update_tier(tier_id: int, organization_id: int, **fields) -> MembershipTier:
    tier = db.session.scalars(
        select(MembershipTier).where(
            MembershipTier.id == tier_id,
            MembershipTier.organization_id == organization_id,
        ).limit(1)
    ).first()
    if tier is None:
        raise NotFound()
    for k, v in fields.items():
        if hasattr(tier, k):
            setattr(tier, k, v)
    db.session.commit()
    return tier


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _compute_end_date(start: date, interval: str) -> date:
    if interval == "monthly":
        m = start.month + 1
        y = start.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        return start.replace(year=y, month=m)
    if interval == "quarterly":
        m = start.month + 3
        y = start.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        return start.replace(year=y, month=m)
    # default: annual
    return start.replace(year=start.year + 1)


def enroll_member(
    organization_id: int,
    donor_id: int,
    tier_id: int,
    *,
    start_date: Optional[date] = None,
    payment_reference: Optional[str] = None,
    notes: Optional[str] = None,
) -> MembershipRecord:
    tier = db.session.scalars(
        select(MembershipTier).where(
            MembershipTier.id == tier_id,
            MembershipTier.organization_id == organization_id,
        ).limit(1)
    ).first()
    if tier is None:
        raise NotFound()
    start = start_date or _today()
    end = _compute_end_date(start, tier.interval)

    # Lapse any existing active record for this donor in this org
    existing = list(db.session.scalars(
        select(MembershipRecord).where(
            MembershipRecord.organization_id == organization_id,
            MembershipRecord.donor_id == donor_id,
            MembershipRecord.status == "active",
        )
    ))
    for old in existing:
        old.status = "lapsed"

    record = MembershipRecord(
        organization_id=organization_id,
        donor_id=donor_id,
        tier_id=tier_id,
        start_date=start,
        end_date=end,
        next_renewal_date=end,
        status="active",
        payment_reference=payment_reference,
        notes=notes,
    )
    db.session.add(record)
    db.session.commit()
    return record


def renew_membership(record_id: int, organization_id: int, *, payment_reference: Optional[str] = None) -> MembershipRecord:
    record = db.session.scalars(
        select(MembershipRecord).where(
            MembershipRecord.id == record_id,
            MembershipRecord.organization_id == organization_id,
        ).limit(1)
    ).first()
    if record is None:
        raise NotFound()
    tier = record.tier
    new_start = record.end_date or _today()
    new_end = _compute_end_date(new_start, tier.interval)
    record.start_date = new_start
    record.end_date = new_end
    record.next_renewal_date = new_end
    record.status = "active"
    if payment_reference:
        record.payment_reference = payment_reference
    db.session.commit()
    return record


def cancel_membership(record_id: int, organization_id: int) -> MembershipRecord:
    record = db.session.scalars(
        select(MembershipRecord).where(
            MembershipRecord.id == record_id,
            MembershipRecord.organization_id == organization_id,
        ).limit(1)
    ).first()
    if record is None:
        raise NotFound()
    record.status = "cancelled"
    db.session.commit()
    return record


def get_member_record(donor_id: int, organization_id: int) -> Optional[MembershipRecord]:
    """Return the most recent active membership record for a donor."""
    stmt = select(MembershipRecord).where(
        MembershipRecord.donor_id == donor_id,
        MembershipRecord.organization_id == organization_id,
        MembershipRecord.status == "active",
    ).order_by(MembershipRecord.start_date.desc()).limit(1)
    return db.session.scalars(stmt).first()


def list_members(
    organization_id: int,
    status: Optional[str] = None,
    tier_id: Optional[int] = None,
) -> List[MembershipRecord]:
    stmt = select(MembershipRecord).where(MembershipRecord.organization_id == organization_id)
    if status:
        stmt = stmt.where(MembershipRecord.status == status)
    if tier_id is not None:
        stmt = stmt.where(MembershipRecord.tier_id == tier_id)
    stmt = stmt.order_by(MembershipRecord.start_date.desc())
    return list(db.session.scalars(stmt))


def expiring_soon(organization_id: int, within_days: int = 30) -> List[MembershipRecord]:
    """Active members whose membership expires within `within_days`."""
    cutoff = _today() + timedelta(days=within_days)
    today = _today()
    stmt = select(MembershipRecord).where(
        MembershipRecord.organization_id == organization_id,
        MembershipRecord.status == "active",
        MembershipRecord.end_date >= today,
        MembershipRecord.end_date <= cutoff,
    ).order_by(MembershipRecord.end_date.asc())
    return list(db.session.scalars(stmt))


def lapse_expired_memberships(organization_id: int) -> int:
    """Mark past-due active memberships as lapsed. Returns count updated."""
    today = _today()
    records = list(db.session.scalars(
        select(MembershipRecord).where(
            MembershipRecord.organization_id == organization_id,
            MembershipRecord.status == "active",
            MembershipRecord.end_date < today,
        )
    ))
    for r in records:
        r.status = "lapsed"
    db.session.commit()
    return len(records)


def membership_summary(organization_id: int) -> dict:
    rows = db.session.connection().exec_driver_sql(
        str(select(
            MembershipRecord.status,
            func.count(MembershipRecord.id).label("count"),
        ).where(MembershipRecord.organization_id == organization_id).group_by(MembershipRecord.status).compile(compile_kwargs={"literal_binds": True}))
    ).all()
    return {status: count for status, count in rows}
