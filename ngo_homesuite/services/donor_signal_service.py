from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from ngo_homesuite.models.core import Donation, Donor, DonorEngagementScore, DonorJourneyAutomationEvent, MembershipRecord, Task, db


def _utc_today() -> datetime.date:
    return datetime.now(timezone.utc).date()


def _signal_priority(churn_risk: float) -> str:
    if churn_risk >= 0.65:
        return "high"
    if churn_risk >= 0.35:
        return "medium"
    return "low"


def _next_action(priority: str) -> str:
    if priority == "high":
        return "Schedule stewardship outreach"
    if priority == "medium":
        return "Send impact update"
    return "Keep warm with periodic stewardship"


def get_donor_signal(org_id: int, *, donor: Donor) -> dict[str, Any]:
    """Return a canonical donor signal payload used across donor-facing services."""
    today = _utc_today()
    last_12m_cutoff = datetime.combine(today - timedelta(days=365), datetime.min.time())

    donations = list(
        db.session.scalars(
            select(Donation)
            .where(Donation.organization_id == int(org_id), Donation.donor_id == int(donor.id))
            .order_by(Donation.donation_date.asc())
        )
    )

    last_gift = donations[-1].donation_date.date() if donations and donations[-1].donation_date else None
    recency_days = (today - last_gift).days if last_gift else 9999
    donations_12m = [d for d in donations if d.donation_date and d.donation_date >= last_12m_cutoff]

    lifetime_total = sum(float(d.amount or 0.0) for d in donations)
    avg_gift = lifetime_total / len(donations) if donations else 0.0
    annualized_value = sum(float(d.amount or 0.0) for d in donations_12m)

    membership = db.session.scalars(
        select(MembershipRecord).where(
            MembershipRecord.organization_id == int(org_id),
            MembershipRecord.donor_id == int(donor.id),
            MembershipRecord.status == "active",
        ).limit(1)
    ).first()

    open_tasks = db.session.scalar(
        select(func.count(Task.id)).where(
            Task.organization_id == int(org_id),
            Task.donor_id == int(donor.id),
            Task.status.in_(["open", "in_progress"]),
        )
    ) or 0

    completed_tasks = db.session.scalar(
        select(func.count(Task.id)).where(
            Task.organization_id == int(org_id),
            Task.donor_id == int(donor.id),
            Task.status == "done",
        )
    ) or 0

    journey_events = db.session.scalar(
        select(func.count(DonorJourneyAutomationEvent.id)).where(
            DonorJourneyAutomationEvent.organization_id == int(org_id),
            DonorJourneyAutomationEvent.donor_id == int(donor.id),
        )
    ) or 0

    engagement_boost = 0.0
    if membership is not None:
        engagement_boost += 0.20
    engagement_boost += min(int(open_tasks) * 0.04, 0.16)
    engagement_boost += min(int(completed_tasks) * 0.02, 0.10)

    churn_risk = min(1.0, round((recency_days / 365.0) * 0.45 + max(0.0, 0.45 - engagement_boost), 4))
    lifetime_value_estimate = max(
        lifetime_total * (1.0 + (1.0 - churn_risk)),
        annualized_value * 2.0,
        avg_gift * max(1.0, 1.0 + (12.0 - min(recency_days / 30.0, 12.0)) / 12.0),
    )
    priority = _signal_priority(churn_risk)

    engagement_score = db.session.scalars(
        select(DonorEngagementScore).where(
            DonorEngagementScore.organization_id == int(org_id),
            DonorEngagementScore.donor_id == int(donor.id),
        ).limit(1)
    ).first()

    return {
        "donor_id": int(donor.id),
        "donor_name": donor.name,
        "email": donor.email,
        "last_gift_at": last_gift.isoformat() if last_gift else None,
        "recency_days": int(recency_days),
        "gifts_12m": int(len(donations_12m)),
        "lifetime_total": round(lifetime_total, 2),
        "annualized_value": round(annualized_value, 2),
        "avg_gift": round(avg_gift, 2),
        "membership_active": bool(membership),
        "open_tasks": int(open_tasks),
        "completed_tasks": int(completed_tasks),
        "journey_events": int(journey_events),
        "churn_risk": round(churn_risk, 4),
        "lifetime_value_estimate": round(float(lifetime_value_estimate), 2),
        "next_action": _next_action(priority),
        "priority": priority,
        "engagement_score": round(float(engagement_score.score or 0.0), 2) if engagement_score else None,
        "segment": engagement_score.segment if engagement_score else None,
        "cultivation_priority": engagement_score.cultivation_priority if engagement_score else None,
        "explanation": engagement_score.explanation if engagement_score else None,
        "signal_version": "v1",
        "signal_generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }


def list_donor_signals(org_id: int, *, limit: int = 10, ascending_engagement: bool = True) -> list[dict[str, Any]]:
    query = select(Donor).where(Donor.organization_id == int(org_id))
    if ascending_engagement:
        score_subquery = (
            select(DonorEngagementScore.donor_id, DonorEngagementScore.score)
            .where(DonorEngagementScore.organization_id == int(org_id))
            .subquery()
        )
        query = query.outerjoin(score_subquery, score_subquery.c.donor_id == Donor.id).order_by(score_subquery.c.score.asc().nullsfirst())
    else:
        query = query.order_by(Donor.created_at.desc())

    donors = list(db.session.scalars(query.limit(max(1, int(limit)))))
    return [get_donor_signal(int(org_id), donor=donor) for donor in donors]
