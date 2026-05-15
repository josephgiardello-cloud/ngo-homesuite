"""Donor Engagement Scoring service.

Computes a 0-100 composite health score from four RFM-adjacent dimensions:
    Recency   (0–25)  — days since last gift
    Frequency (0–25)  — number of gifts in last 3 years
    Monetary  (0–25)  — total lifetime giving vs org median
    Engagement(0–25)  — membership, open tasks, events, journey activity

Segments donors: champion | loyal | promising | at_risk | lapsed | new
Sets cultivation_priority: high | medium | low

Call `compute_score(org_id, donor_id)` to recalculate and persist.
Call `batch_recompute(org_id)` to refresh all donors for an org.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import func

from ngo_homesuite.models.core import (
    Donation,
    Donor,
    DonorEngagementScore,
    MembershipRecord,
    Task,
    db,
)

logger = logging.getLogger(__name__)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Dimension calculators (each returns 0.0–25.0)
# ---------------------------------------------------------------------------

def _recency_score(org_id: int, donor_id: int) -> float:
    """Score based on days since last donation (25 = gave within 30 days, 0 = never gave)."""
    last = (
        db.session.query(func.max(Donation.donation_date))
        .filter(Donation.organization_id == org_id, Donation.donor_id == donor_id)
        .scalar()
    )
    if not last:
        return 0.0
    last_date = last.date() if hasattr(last, "date") else last
    days_ago = (_today() - last_date).days
    if days_ago <= 30:
        return 25.0
    if days_ago <= 90:
        return 20.0
    if days_ago <= 180:
        return 15.0
    if days_ago <= 365:
        return 10.0
    if days_ago <= 730:
        return 5.0
    return 1.0


def _frequency_score(org_id: int, donor_id: int) -> float:
    """Score based on # of gifts in rolling 3-year window (25 = 10+ gifts)."""
    cutoff = _today() - timedelta(days=3 * 365)
    count = (
        db.session.query(func.count(Donation.id))
        .filter(
            Donation.organization_id == org_id,
            Donation.donor_id == donor_id,
            Donation.donation_date >= datetime.combine(cutoff, datetime.min.time()),
        )
        .scalar()
        or 0
    )
    if count >= 10:
        return 25.0
    if count >= 7:
        return 20.0
    if count >= 4:
        return 15.0
    if count >= 2:
        return 10.0
    if count >= 1:
        return 5.0
    return 0.0


def _monetary_score(org_id: int, donor_id: int, org_median: float) -> float:
    """Score based on lifetime giving vs org median (25 = 5× median)."""
    total = (
        db.session.query(func.coalesce(func.sum(Donation.amount), 0.0))
        .filter(Donation.organization_id == org_id, Donation.donor_id == donor_id)
        .scalar()
    )
    total = float(total or 0)
    if org_median <= 0:
        return 12.5 if total > 0 else 0.0
    ratio = total / org_median
    if ratio >= 5.0:
        return 25.0
    if ratio >= 3.0:
        return 20.0
    if ratio >= 2.0:
        return 15.0
    if ratio >= 1.0:
        return 10.0
    if total > 0:
        return 5.0
    return 0.0


def _engagement_score(org_id: int, donor_id: int) -> float:
    """Score based on non-donation engagement signals (0–25)."""
    score = 0.0

    # Active membership
    active_membership = MembershipRecord.query.filter_by(
        organization_id=org_id, donor_id=donor_id, status="active"
    ).first()
    if active_membership:
        score += 10.0

    # Open/in-progress tasks linked to this donor
    open_tasks = Task.query.filter(
        Task.organization_id == org_id,
        Task.donor_id == donor_id,
        Task.status.in_(["open", "in_progress"]),
    ).count()
    score += min(open_tasks * 2.5, 7.5)

    # Recent email/task completions (signal of active relationship)
    completed_tasks = Task.query.filter(
        Task.organization_id == org_id,
        Task.donor_id == donor_id,
        Task.status == "done",
        Task.completed_at >= datetime.combine(_today() - timedelta(days=180), datetime.min.time()),
    ).count()
    score += min(completed_tasks * 1.25, 7.5)

    return min(score, 25.0)


def _org_median_giving(org_id: int) -> float:
    """Approximate org-level median lifetime giving per donor."""
    totals = (
        db.session.query(func.coalesce(func.sum(Donation.amount), 0.0))
        .filter(Donation.organization_id == org_id, Donation.donor_id.isnot(None))
        .group_by(Donation.donor_id)
        .all()
    )
    if not totals:
        return 0.0
    vals = sorted(float(r[0]) for r in totals)
    mid = len(vals) // 2
    return vals[mid] if vals else 0.0


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

def _classify(score: float, recency: float, frequency: float) -> tuple[str, str]:
    """Return (segment, cultivation_priority)."""
    if score >= 80:
        return "champion", "high"
    if score >= 60 and recency >= 15:
        return "loyal", "medium"
    if score >= 45:
        return "promising", "medium"
    if score >= 30 and recency < 10:
        return "at_risk", "high"
    if score < 15 and recency <= 1:
        return "lapsed", "high"
    return "new", "low"


def _build_explanation(
    recency: float, frequency: float, monetary: float, engagement: float, total: float
) -> str:
    parts = [
        f"Recency {recency:.0f}/25",
        f"Frequency {frequency:.0f}/25",
        f"Monetary {monetary:.0f}/25",
        f"Engagement {engagement:.0f}/25",
        f"Total {total:.1f}/100",
    ]
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_score(org_id: int, donor_id: int) -> DonorEngagementScore:
    """Compute and persist the engagement score for one donor."""
    org_median = _org_median_giving(org_id)

    r = _recency_score(org_id, donor_id)
    f = _frequency_score(org_id, donor_id)
    m = _monetary_score(org_id, donor_id, org_median)
    e = _engagement_score(org_id, donor_id)
    total = round(r + f + m + e, 2)
    segment, priority = _classify(total, r, f)
    explanation = _build_explanation(r, f, m, e, total)

    existing = DonorEngagementScore.query.filter_by(
        organization_id=org_id, donor_id=donor_id
    ).first()
    if existing:
        existing.score = total
        existing.recency_score = r
        existing.frequency_score = f
        existing.monetary_score = m
        existing.engagement_score = e
        existing.segment = segment
        existing.cultivation_priority = priority
        existing.explanation = explanation
        existing.computed_at = _utcnow()
        db.session.commit()
        return existing

    record = DonorEngagementScore(
        organization_id=org_id,
        donor_id=donor_id,
        score=total,
        recency_score=r,
        frequency_score=f,
        monetary_score=m,
        engagement_score=e,
        segment=segment,
        cultivation_priority=priority,
        explanation=explanation,
    )
    db.session.add(record)
    db.session.commit()
    return record


def batch_recompute(org_id: int) -> Dict[str, int]:
    """Recompute scores for all donors in an org. Returns counts."""
    donors = Donor.query.filter_by(organization_id=org_id).all()
    updated = 0
    errors = 0
    for donor in donors:
        try:
            compute_score(org_id, donor.id)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Score compute failed donor=%s: %s", donor.id, exc)
            errors += 1
    return {"updated": updated, "errors": errors}


def get_score(org_id: int, donor_id: int) -> Optional[DonorEngagementScore]:
    return DonorEngagementScore.query.filter_by(
        organization_id=org_id, donor_id=donor_id
    ).first()


def top_donors_by_score(org_id: int, limit: int = 20, segment: Optional[str] = None):
    q = DonorEngagementScore.query.filter_by(organization_id=org_id)
    if segment:
        q = q.filter_by(segment=segment)
    return q.order_by(DonorEngagementScore.score.desc()).limit(limit).all()


def high_priority_lapsed(org_id: int, limit: int = 50):
    """At-risk and lapsed donors sorted by score desc (most valuable to reactivate first)."""
    return (
        DonorEngagementScore.query.filter(
            DonorEngagementScore.organization_id == org_id,
            DonorEngagementScore.segment.in_(["at_risk", "lapsed"]),
        )
        .order_by(DonorEngagementScore.score.desc())
        .limit(limit)
        .all()
    )
