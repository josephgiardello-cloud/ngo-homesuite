"""Advanced reporting service.

Covers:
- LYBUNT (Last Year But Unfortunately Not This year) donor segments
- SYBUNT (Some Year But Unfortunately Not This year) donor segments
- 360Â° supporter timeline â€” all touchpoints for a single donor
- Pipeline/funnel snapshots for grants and memberships
- Scheduled report CRUD and next-run orchestration
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from ngo_homesuite.models.core import (
    Donation,
    Donor,
    Grant,
    GrantDisbursement,
    MembershipRecord,
    ScheduledReport,
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
    gave_last_year = select(Donation.donor_id).where(
        Donation.organization_id == organization_id,
        Donation.donor_id.isnot(None),
        func.strftime("%Y", Donation.donation_date) == str(last_year),
    ).distinct().subquery()

    # Donors who donated in reference_year (this year)
    gave_this_year = select(Donation.donor_id).where(
        Donation.organization_id == organization_id,
        Donation.donor_id.isnot(None),
        func.strftime("%Y", Donation.donation_date) == str(year),
    ).distinct().subquery()

    # LYBUNT = gave last year AND NOT gave this year
    lybunt_ids = select(gave_last_year.c.donor_id).where(
        gave_last_year.c.donor_id.notin_(select(gave_this_year.c.donor_id))
    ).subquery()

    # Aggregate last-year gifts per donor
    agg = select(
        Donation.donor_id,
        func.sum(Donation.amount).label("last_year_total"),
        func.max(Donation.donation_date).label("last_gift_date"),
        func.count(Donation.id).label("gift_count"),
    ).where(
        Donation.organization_id == organization_id,
        Donation.donor_id.in_(select(lybunt_ids.c.donor_id)),
        func.strftime("%Y", Donation.donation_date) == str(last_year),
    ).group_by(Donation.donor_id).subquery()

    rows = db.session.connection().exec_driver_sql(
        str(select(Donor, agg).join(agg, Donor.id == agg.c.donor_id).where(Donor.organization_id == organization_id).order_by(agg.c.last_year_total.desc()).compile(compile_kwargs={"literal_binds": True}))
    ).all()

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
    gave_some_year = select(Donation.donor_id).where(
        Donation.organization_id == organization_id,
        Donation.donor_id.isnot(None),
        func.strftime("%Y", Donation.donation_date) < str(year),
    ).distinct().subquery()

    # Donors who donated in reference_year
    gave_this_year = select(Donation.donor_id).where(
        Donation.organization_id == organization_id,
        Donation.donor_id.isnot(None),
        func.strftime("%Y", Donation.donation_date) == str(year),
    ).distinct().subquery()

    sybunt_ids = select(gave_some_year.c.donor_id).where(
        gave_some_year.c.donor_id.notin_(select(gave_this_year.c.donor_id))
    ).subquery()

    agg = select(
        Donation.donor_id,
        func.sum(Donation.amount).label("lifetime_total"),
        func.max(Donation.donation_date).label("last_gift_date"),
        func.count(Donation.id).label("gift_count"),
    ).where(
        Donation.organization_id == organization_id,
        Donation.donor_id.in_(select(sybunt_ids.c.donor_id)),
    ).group_by(Donation.donor_id).subquery()

    rows = db.session.connection().exec_driver_sql(
        str(select(Donor, agg).join(agg, Donor.id == agg.c.donor_id).where(Donor.organization_id == organization_id).order_by(agg.c.last_gift_date.desc()).compile(compile_kwargs={"literal_binds": True}))
    ).all()

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
# 360Â° Supporter Timeline
# ---------------------------------------------------------------------------

def get_supporter_timeline(organization_id: int, donor_id: int) -> Dict[str, Any]:
    """Aggregate all touchpoints for a donor into a chronological timeline."""
    donor = db.session.scalars(
        select(Donor).where(Donor.id == donor_id, Donor.organization_id == organization_id).limit(1)
    ).first()
    if donor is None:
        raise NotFound()
    events: List[Dict[str, Any]] = []

    # Donations
    donations = list(db.session.scalars(
        select(Donation).where(Donation.organization_id == organization_id, Donation.donor_id == donor_id).order_by(Donation.donation_date.asc())
    ))
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
    memberships = list(db.session.scalars(
        select(MembershipRecord).where(MembershipRecord.organization_id == organization_id, MembershipRecord.donor_id == donor_id).order_by(MembershipRecord.start_date.asc())
    ))
    for m in memberships:
        events.append(
            {
                "type": "membership",
                "date": str(m.start_date),
                "summary": f"Membership [{m.status}] â€” tier {m.tier_id}",
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
        return set(db.session.scalars(
            select(Donation.donor_id).where(
                Donation.organization_id == organization_id,
                Donation.donor_id.isnot(None),
                func.strftime("%Y", Donation.donation_date) == str(y),
            ).distinct()
        ).all())

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
    rows = db.session.connection().exec_driver_sql(
        str(select(
            func.strftime("%Y", Donation.donation_date).label("year"),
            func.count(Donation.id).label("gift_count"),
            func.count(func.distinct(Donation.donor_id)).label("donor_count"),
            func.sum(Donation.amount).label("total"),
        ).where(Donation.organization_id == organization_id).group_by(func.strftime("%Y", Donation.donation_date)).order_by(text("year ASC")).compile(compile_kwargs={"literal_binds": True}))
    ).all()
    return [
        {
            "year": r.year,
            "gift_count": r.gift_count,
            "donor_count": r.donor_count,
            "total": float(r.total or 0),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Funder Report
# ---------------------------------------------------------------------------

def funder_report(
    organization_id: int,
    funder_name: str,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Generate a funder-specific report covering donations, grants, and impact.

    Returns a dict suitable for JSON serialisation or PDF templating.
    """
    today = _today()
    start = start_date or date(today.year, 1, 1)
    end = end_date or today

    # Donations attributed to funder (by donor name match)
    donation_rows = list(db.session.scalars(
        select(Donation).where(
            Donation.organization_id == organization_id,
            Donation.donation_date >= datetime(start.year, start.month, start.day),
            Donation.donation_date <= datetime(end.year, end.month, end.day, 23, 59, 59),
            Donation.donor_name.ilike(f"%{funder_name}%"),
        )
    ))
    total_donated = sum(d.amount for d in donation_rows)

    # Grants from this funder
    grant_rows = list(db.session.scalars(
        select(Grant).where(
            Grant.organization_id == organization_id,
            Grant.funder_name.ilike(f"%{funder_name}%"),
        )
    ))
    total_granted = sum(g.amount_awarded or 0.0 for g in grant_rows)
    total_requested = sum(g.amount_requested or 0.0 for g in grant_rows)

    # Disbursements within date range
    disbursement_rows = list(db.session.scalars(
        select(GrantDisbursement).join(Grant).where(
            Grant.organization_id == organization_id,
            Grant.funder_name.ilike(f"%{funder_name}%"),
            GrantDisbursement.received_date >= start,
            GrantDisbursement.received_date <= end,
        )
    ))
    total_disbursed = sum(d.amount for d in disbursement_rows)

    return {
        "funder_name": funder_name,
        "organization_id": organization_id,
        "report_period": {"start": start.isoformat(), "end": end.isoformat()},
        "donations": {
            "count": len(donation_rows),
            "total": round(total_donated, 2),
            "items": [
                {
                    "id": d.id,
                    "amount": d.amount,
                    "currency": d.currency,
                    "donation_date": d.donation_date.isoformat() if d.donation_date else None,
                    "purpose": d.purpose,
                }
                for d in donation_rows
            ],
        },
        "grants": {
            "count": len(grant_rows),
            "total_requested": round(total_requested, 2),
            "total_awarded": round(total_granted, 2),
            "total_disbursed_in_period": round(total_disbursed, 2),
            "items": [
                {
                    "id": g.id,
                    "title": g.title,
                    "status": g.status,
                    "amount_requested": g.amount_requested,
                    "amount_awarded": g.amount_awarded,
                    "application_deadline": g.application_deadline.isoformat() if g.application_deadline else None,
                    "award_date": g.award_date.isoformat() if g.award_date else None,
                }
                for g in grant_rows
            ],
        },
        "summary": {
            "total_funding": round(total_donated + total_granted, 2),
        },
    }


# ---------------------------------------------------------------------------
# Scheduled Reports CRUD
# ---------------------------------------------------------------------------

def create_scheduled_report(
    organization_id: int,
    name: str,
    report_type: str,
    frequency: str,
    *,
    delivery_email: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
    created_by_id: Optional[int] = None,
) -> ScheduledReport:
    from datetime import datetime as dt, timezone as tz, timedelta as td

    _freq_delta = {"daily": td(days=1), "weekly": td(weeks=1), "monthly": td(days=30), "quarterly": td(days=90)}
    now = dt.now(tz.utc).replace(tzinfo=None)
    delta = _freq_delta.get(frequency, td(days=30))
    report = ScheduledReport(
        organization_id=organization_id,
        created_by_id=created_by_id,
        name=name,
        report_type=report_type,
        frequency=frequency,
        delivery_email=delivery_email,
        parameters=parameters,
        next_run_at=now + delta,
    )
    db.session.add(report)
    db.session.commit()
    return report


def list_scheduled_reports(organization_id: int) -> List[ScheduledReport]:
    return list(db.session.scalars(
        select(ScheduledReport).where(ScheduledReport.organization_id == organization_id).order_by(ScheduledReport.created_at.desc())
    ))


def update_scheduled_report(report_id: int, organization_id: int, **fields) -> ScheduledReport:
    report = db.session.scalars(
        select(ScheduledReport).where(ScheduledReport.id == report_id, ScheduledReport.organization_id == organization_id).limit(1)
    ).first()
    if report is None:
        raise NotFound()
    allowed = {"name", "report_type", "frequency", "delivery_email", "parameters", "is_active"}
    for key, value in fields.items():
        if key in allowed:
            setattr(report, key, value)
    db.session.commit()
    return report


def delete_scheduled_report(report_id: int, organization_id: int) -> None:
    report = db.session.scalars(
        select(ScheduledReport).where(ScheduledReport.id == report_id, ScheduledReport.organization_id == organization_id).limit(1)
    ).first()
    if report is None:
        raise NotFound()
    db.session.delete(report)
    db.session.commit()

