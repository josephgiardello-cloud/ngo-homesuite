"""Advanced reporting service.

Covers:
- LYBUNT (Last Year But Unfortunately Not This year) donor segments
- SYBUNT (Some Year But Unfortunately Not This year) donor segments
- 360° supporter timeline — all touchpoints for a single donor
- Pipeline/funnel snapshots for grants and memberships
- Scheduled report stubs
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, text

from ngo_homesuite.models.core import (
    Donation,
    Donor,
    Grant,
    GrantDisbursement,
    MembershipRecord,
    db,
)


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# LYBUNT / SYBUNT
# ---------------------------------------------------------------------------

def get_lybunt_donors(organization_id: int, reference_year: Optional[int] = None) -> List[Dict[str, Any]]:
    """Donors who gave last year but have NOT given in the current (or reference) year.

    Returns list of dicts with donor info and their last gift details.
    """
    year = reference_year or _today().year
    last_year = year - 1

    # Donors who donated in last_year
    gave_last_year = (
        db.session.query(Donation.donor_id)
        .filter(
            Donation.organization_id == organization_id,
            Donation.donor_id.isnot(None),
            func.strftime("%Y", Donation.donation_date) == str(last_year),
        )
        .distinct()
        .subquery()
    )

    # Donors who donated in reference_year (this year)
    gave_this_year = (
        db.session.query(Donation.donor_id)
        .filter(
            Donation.organization_id == organization_id,
            Donation.donor_id.isnot(None),
            func.strftime("%Y", Donation.donation_date) == str(year),
        )
        .distinct()
        .subquery()
    )

    # LYBUNT = gave last year AND NOT gave this year
    lybunt_ids = (
        db.session.query(gave_last_year.c.donor_id)
        .filter(gave_last_year.c.donor_id.notin_(db.session.query(gave_this_year.c.donor_id)))
        .subquery()
    )

    # Aggregate last-year gifts per donor
    agg = (
        db.session.query(
            Donation.donor_id,
            func.sum(Donation.amount).label("last_year_total"),
            func.max(Donation.donation_date).label("last_gift_date"),
            func.count(Donation.id).label("gift_count"),
        )
        .filter(
            Donation.organization_id == organization_id,
            Donation.donor_id.in_(db.session.query(lybunt_ids.c.donor_id)),
            func.strftime("%Y", Donation.donation_date) == str(last_year),
        )
        .group_by(Donation.donor_id)
        .subquery()
    )

    rows = (
        db.session.query(Donor, agg)
        .join(agg, Donor.id == agg.c.donor_id)
        .filter(Donor.organization_id == organization_id)
        .order_by(agg.c.last_year_total.desc())
        .all()
    )

    return [
        {
            "donor_id": donor.id,
            "name": donor.name,
            "email": donor.email,
            "phone": donor.phone,
            "last_year_total": float(row.last_year_total or 0),
            "last_gift_date": str(row.last_gift_date)[:10] if row.last_gift_date else None,
            "gift_count_last_year": row.gift_count,
            "segment": "LYBUNT",
        }
        for donor, row in rows
    ]


def get_sybunt_donors(organization_id: int, reference_year: Optional[int] = None) -> List[Dict[str, Any]]:
    """Donors who gave in some prior year but NOT in the current (or reference) year."""
    year = reference_year or _today().year

    # Donors who gave in any year before reference_year
    gave_some_year = (
        db.session.query(Donation.donor_id)
        .filter(
            Donation.organization_id == organization_id,
            Donation.donor_id.isnot(None),
            func.strftime("%Y", Donation.donation_date) < str(year),
        )
        .distinct()
        .subquery()
    )

    # Donors who donated in reference_year
    gave_this_year = (
        db.session.query(Donation.donor_id)
        .filter(
            Donation.organization_id == organization_id,
            Donation.donor_id.isnot(None),
            func.strftime("%Y", Donation.donation_date) == str(year),
        )
        .distinct()
        .subquery()
    )

    sybunt_ids = (
        db.session.query(gave_some_year.c.donor_id)
        .filter(gave_some_year.c.donor_id.notin_(db.session.query(gave_this_year.c.donor_id)))
        .subquery()
    )

    agg = (
        db.session.query(
            Donation.donor_id,
            func.sum(Donation.amount).label("lifetime_total"),
            func.max(Donation.donation_date).label("last_gift_date"),
            func.count(Donation.id).label("gift_count"),
        )
        .filter(
            Donation.organization_id == organization_id,
            Donation.donor_id.in_(db.session.query(sybunt_ids.c.donor_id)),
        )
        .group_by(Donation.donor_id)
        .subquery()
    )

    rows = (
        db.session.query(Donor, agg)
        .join(agg, Donor.id == agg.c.donor_id)
        .filter(Donor.organization_id == organization_id)
        .order_by(agg.c.last_gift_date.desc())
        .all()
    )

    return [
        {
            "donor_id": donor.id,
            "name": donor.name,
            "email": donor.email,
            "phone": donor.phone,
            "lifetime_total": float(row.lifetime_total or 0),
            "last_gift_date": str(row.last_gift_date)[:10] if row.last_gift_date else None,
            "total_gifts": row.gift_count,
            "segment": "SYBUNT",
        }
        for donor, row in rows
    ]


# ---------------------------------------------------------------------------
# 360° Supporter Timeline
# ---------------------------------------------------------------------------

def get_supporter_timeline(organization_id: int, donor_id: int) -> Dict[str, Any]:
    """Aggregate all touchpoints for a donor into a chronological timeline."""
    donor = Donor.query.filter_by(id=donor_id, organization_id=organization_id).first_or_404()
    events: List[Dict[str, Any]] = []

    # Donations
    donations = Donation.query.filter_by(
        organization_id=organization_id, donor_id=donor_id
    ).order_by(Donation.donation_date.asc()).all()
    for d in donations:
        events.append(
            {
                "type": "donation",
                "date": str(d.donation_date)[:10],
                "summary": f"Donated {d.currency} {d.amount:,.2f}",
                "detail": {
                    "amount": d.amount,
                    "currency": d.currency,
                    "method": d.payment_method,
                    "purpose": d.purpose,
                    "status": d.status,
                    "id": d.id,
                },
            }
        )

    # Memberships
    memberships = MembershipRecord.query.filter_by(
        organization_id=organization_id, donor_id=donor_id
    ).order_by(MembershipRecord.start_date.asc()).all()
    for m in memberships:
        events.append(
            {
                "type": "membership",
                "date": str(m.start_date),
                "summary": f"Membership [{m.status}] — tier {m.tier_id}",
                "detail": {
                    "tier_id": m.tier_id,
                    "start_date": str(m.start_date),
                    "end_date": str(m.end_date) if m.end_date else None,
                    "status": m.status,
                    "id": m.id,
                },
            }
        )

    # Sort all events chronologically
    events.sort(key=lambda e: e["date"])

    # Aggregate stats
    total_given = sum(d.amount for d in donations)
    first_gift = min((d.donation_date for d in donations), default=None)
    last_gift = max((d.donation_date for d in donations), default=None)

    current_membership = next(
        (m for m in memberships if m.status == "active"), None
    )

    return {
        "donor": {
            "id": donor.id,
            "name": donor.name,
            "email": donor.email,
            "phone": donor.phone,
            "donor_type": donor.donor_type,
        },
        "stats": {
            "total_given": total_given,
            "gift_count": len(donations),
            "first_gift_date": str(first_gift)[:10] if first_gift else None,
            "last_gift_date": str(last_gift)[:10] if last_gift else None,
            "years_giving": _years_giving(donations),
            "current_membership_tier": current_membership.tier_id if current_membership else None,
            "membership_status": current_membership.status if current_membership else "none",
        },
        "timeline": events,
    }


def _years_giving(donations: list) -> int:
    if not donations:
        return 0
    years = {str(d.donation_date)[:4] for d in donations}
    return len(years)


# ---------------------------------------------------------------------------
# Retention / Generosity summary
# ---------------------------------------------------------------------------

def donor_retention_rate(organization_id: int, year: Optional[int] = None) -> Dict[str, Any]:
    """Calculate year-over-year donor retention rate."""
    current_year = year or _today().year
    prior_year = current_year - 1

    def _donor_ids_for_year(y: int):
        return {
            row[0]
            for row in db.session.query(Donation.donor_id)
            .filter(
                Donation.organization_id == organization_id,
                Donation.donor_id.isnot(None),
                func.strftime("%Y", Donation.donation_date) == str(y),
            )
            .distinct()
            .all()
        }

    prior = _donor_ids_for_year(prior_year)
    current = _donor_ids_for_year(current_year)
    retained = prior & current
    new = current - prior
    lapsed = prior - current
    rate = (len(retained) / len(prior) * 100) if prior else 0.0

    return {
        "reference_year": current_year,
        "prior_year_donors": len(prior),
        "current_year_donors": len(current),
        "retained_donors": len(retained),
        "new_donors": len(new),
        "lapsed_donors": len(lapsed),
        "retention_rate_pct": round(rate, 1),
    }


def giving_summary_by_year(organization_id: int) -> List[Dict[str, Any]]:
    """Year-by-year donation totals and donor counts."""
    rows = (
        db.session.query(
            func.strftime("%Y", Donation.donation_date).label("year"),
            func.count(Donation.id).label("gift_count"),
            func.count(func.distinct(Donation.donor_id)).label("donor_count"),
            func.sum(Donation.amount).label("total"),
        )
        .filter(Donation.organization_id == organization_id)
        .group_by(func.strftime("%Y", Donation.donation_date))
        .order_by(text("year ASC"))
        .all()
    )
    return [
        {
            "year": r.year,
            "gift_count": r.gift_count,
            "donor_count": r.donor_count,
            "total": float(r.total or 0),
        }
        for r in rows
    ]
