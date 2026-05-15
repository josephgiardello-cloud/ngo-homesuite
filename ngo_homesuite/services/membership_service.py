"""Membership management service — tiers, records, auto-renewal tracking."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func

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
    q = MembershipTier.query.filter_by(organization_id=organization_id)
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(MembershipTier.price.asc()).all()


def get_tier(tier_id: int, organization_id: int) -> Optional[MembershipTier]:
    return MembershipTier.query.filter_by(id=tier_id, organization_id=organization_id).first()


def update_tier(tier_id: int, organization_id: int, **fields) -> MembershipTier:
    tier = MembershipTier.query.filter_by(id=tier_id, organization_id=organization_id).first_or_404()
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
    tier = MembershipTier.query.filter_by(id=tier_id, organization_id=organization_id).first_or_404()
    start = start_date or _today()
    end = _compute_end_date(start, tier.interval)

    # Lapse any existing active record for this donor in this org
    existing = MembershipRecord.query.filter_by(
        organization_id=organization_id, donor_id=donor_id, status="active"
    ).all()
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
    record = MembershipRecord.query.filter_by(id=record_id, organization_id=organization_id).first_or_404()
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
    record = MembershipRecord.query.filter_by(id=record_id, organization_id=organization_id).first_or_404()
    record.status = "cancelled"
    db.session.commit()
    return record


def get_member_record(donor_id: int, organization_id: int) -> Optional[MembershipRecord]:
    """Return the most recent active membership record for a donor."""
    return (
        MembershipRecord.query.filter_by(
            donor_id=donor_id, organization_id=organization_id, status="active"
        )
        .order_by(MembershipRecord.start_date.desc())
        .first()
    )


def list_members(
    organization_id: int,
    status: Optional[str] = None,
    tier_id: Optional[int] = None,
) -> List[MembershipRecord]:
    q = MembershipRecord.query.filter_by(organization_id=organization_id)
    if status:
        q = q.filter_by(status=status)
    if tier_id is not None:
        q = q.filter_by(tier_id=tier_id)
    return q.order_by(MembershipRecord.start_date.desc()).all()


def expiring_soon(organization_id: int, within_days: int = 30) -> List[MembershipRecord]:
    """Active members whose membership expires within `within_days`."""
    cutoff = _today() + timedelta(days=within_days)
    today = _today()
    return (
        MembershipRecord.query.filter(
            MembershipRecord.organization_id == organization_id,
            MembershipRecord.status == "active",
            MembershipRecord.end_date >= today,
            MembershipRecord.end_date <= cutoff,
        )
        .order_by(MembershipRecord.end_date.asc())
        .all()
    )


def lapse_expired_memberships(organization_id: int) -> int:
    """Mark past-due active memberships as lapsed. Returns count updated."""
    today = _today()
    records = MembershipRecord.query.filter(
        MembershipRecord.organization_id == organization_id,
        MembershipRecord.status == "active",
        MembershipRecord.end_date < today,
    ).all()
    for r in records:
        r.status = "lapsed"
    db.session.commit()
    return len(records)


def membership_summary(organization_id: int) -> dict:
    rows = (
        db.session.query(
            MembershipRecord.status,
            func.count(MembershipRecord.id).label("count"),
        )
        .filter_by(organization_id=organization_id)
        .group_by(MembershipRecord.status)
        .all()
    )
    return {r.status: r.count for r in rows}
