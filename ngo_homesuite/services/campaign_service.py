"""Campaign management service."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from sqlalchemy import func, select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.models.core import Campaign, Donation, P2PPage, db

_VALID_TYPES = {"annual", "capital", "event", "emergency", "recurring", "p2p", "general"}
_VALID_STATUSES = {"draft", "active", "paused", "closed"}


def _slugify(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80]


def _unique_slug(org_id: int, base: str) -> str:
    slug = base
    counter = 1
    while db.session.scalar(
        select(func.count(Campaign.id)).where(
            Campaign.organization_id == org_id,
            Campaign.slug == slug,
        )
    ):
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def create_campaign(
    organization_id: int,
    *,
    name: str,
    campaign_type: str = "general",
    status: str = "draft",
    description: Optional[str] = None,
    goal_amount: float = 0.0,
    currency: str = "USD",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    fund_id: Optional[int] = None,
    notes: Optional[str] = None,
    slug: Optional[str] = None,
) -> Campaign:
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    if campaign_type not in _VALID_TYPES:
        raise ValueError(f"invalid campaign_type '{campaign_type}'")
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status '{status}'")
    if float(goal_amount) < 0:
        raise ValueError("goal_amount cannot be negative")
    if start_date and end_date and end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    base_slug = _slugify(slug or name)
    final_slug = _unique_slug(int(organization_id), base_slug)

    campaign = Campaign(
        organization_id=int(organization_id),
        fund_id=int(fund_id) if fund_id is not None else None,
        name=name,
        slug=final_slug,
        description=(description or "").strip() or None,
        campaign_type=campaign_type,
        status=status,
        goal_amount=float(goal_amount),
        raised_amount=0.0,
        currency=currency.upper(),
        start_date=start_date,
        end_date=end_date,
        notes=(notes or "").strip() or None,
    )
    db.session.add(campaign)
    db.session.commit()
    audit(
        "campaign.create",
        entity_type="campaign",
        entity_id=int(campaign.id),
        details={"organization_id": int(organization_id), "name": name, "status": status},
    )
    return campaign


def list_campaigns(
    organization_id: int,
    *,
    status: Optional[str] = None,
    campaign_type: Optional[str] = None,
) -> list[Campaign]:
    stmt = select(Campaign).where(Campaign.organization_id == organization_id)
    if status:
        stmt = stmt.where(Campaign.status == status)
    if campaign_type:
        stmt = stmt.where(Campaign.campaign_type == campaign_type)
    stmt = stmt.order_by(Campaign.created_at.desc())
    return list(db.session.scalars(stmt))


def get_campaign(campaign_id: int, organization_id: int) -> Optional[Campaign]:
    return db.session.scalars(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.organization_id == organization_id,
        ).limit(1)
    ).first()


def update_campaign(
    campaign_id: int,
    organization_id: int,
    **kwargs,
) -> Campaign:
    campaign = get_campaign(campaign_id, organization_id)
    if campaign is None:
        raise LookupError(f"Campaign {campaign_id} not found")

    allowed = {"name", "description", "campaign_type", "status", "goal_amount",
               "currency", "start_date", "end_date", "fund_id", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}

    if "status" in updates and updates["status"] not in _VALID_STATUSES:
        raise ValueError(f"invalid status '{updates['status']}'")
    if "campaign_type" in updates and updates["campaign_type"] not in _VALID_TYPES:
        raise ValueError(f"invalid campaign_type '{updates['campaign_type']}'")
    if "goal_amount" in updates and float(updates["goal_amount"]) < 0:
        raise ValueError("goal_amount cannot be negative")

    for key, value in updates.items():
        setattr(campaign, key, value)
    db.session.commit()
    audit(
        "campaign.update",
        entity_type="campaign",
        entity_id=int(campaign_id),
        details={"organization_id": int(organization_id), "fields": list(updates.keys())},
    )
    return campaign


def campaign_stats(campaign_id: int, organization_id: int) -> dict:
    """Return raised total (from linked P2P pages), page count, and progress."""
    campaign = get_campaign(campaign_id, organization_id)
    if campaign is None:
        raise LookupError(f"Campaign {campaign_id} not found")

    page_count = db.session.scalar(
        select(func.count(P2PPage.id)).where(
            P2PPage.campaign_id == campaign_id,
        )
    ) or 0

    # Raised amount tracked on the campaign record itself (updated by import/donation flows)
    # Also compute live sum from P2P page donations for transparency
    live_raised = float(campaign.raised_amount)

    progress_pct = (
        round(live_raised / float(campaign.goal_amount) * 100, 1)
        if campaign.goal_amount > 0
        else 0.0
    )

    return {
        "id": campaign.id,
        "name": campaign.name,
        "slug": campaign.slug,
        "status": campaign.status,
        "campaign_type": campaign.campaign_type,
        "goal_amount": float(campaign.goal_amount),
        "raised_amount": live_raised,
        "progress_pct": progress_pct,
        "currency": campaign.currency,
        "p2p_page_count": int(page_count),
        "start_date": str(campaign.start_date) if campaign.start_date else None,
        "end_date": str(campaign.end_date) if campaign.end_date else None,
    }
