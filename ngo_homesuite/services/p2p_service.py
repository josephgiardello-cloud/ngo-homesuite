"""Peer-to-Peer Fundraising service.

Supports creating public fundraising pages, linking incoming donations,
tracking progress toward a goal, and listing active pages.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func

from ngo_homesuite.models.core import Donation, Donor, P2PPage, P2PPageDonation, db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Slug utilities
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text)


def _unique_slug(base: str) -> str:
    slug = _slugify(base)
    if not P2PPage.query.filter_by(public_slug=slug).first():
        return slug
    import secrets
    return f"{slug}-{secrets.token_urlsafe(4)}"


# ---------------------------------------------------------------------------
# Page CRUD
# ---------------------------------------------------------------------------

def create_page(
    organization_id: int,
    donor_id: int,
    title: str,
    *,
    goal_amount: Optional[float] = None,
    story: Optional[str] = None,
    campaign_slug: Optional[str] = None,
    slug: Optional[str] = None,
) -> P2PPage:
    donor = Donor.query.filter_by(id=donor_id, organization_id=organization_id).first()
    if donor is None:
        raise ValueError("invalid resource reference")

    public_slug = _unique_slug(slug or title)
    page = P2PPage(
        organization_id=organization_id,
        donor_id=donor_id,
        title=title,
        story=story,
        goal_amount=goal_amount or 0.0,
        campaign_slug=campaign_slug,
        public_slug=public_slug,
        status="draft",
    )
    db.session.add(page)
    db.session.commit()
    try:
        from ngo_homesuite.db.utils import audit

        audit(
            "p2p.page.create",
            entity_type="p2p_page",
            entity_id=page.id,
            details={
                "organization_id": organization_id,
                "donor_id": donor_id,
                "title": title,
                "public_slug": page.public_slug,
            },
        )
    except Exception:
        pass
    return page


def publish_page(page_id: int, organization_id: int) -> P2PPage:
    page = P2PPage.query.filter_by(id=page_id, organization_id=organization_id).first_or_404()
    page.status = "active"
    db.session.commit()
    try:
        from ngo_homesuite.db.utils import audit

        audit(
            "p2p.page.publish",
            entity_type="p2p_page",
            entity_id=page.id,
            details={"organization_id": organization_id, "status": page.status},
        )
    except Exception:
        pass
    return page


def close_page(page_id: int, organization_id: int) -> P2PPage:
    page = P2PPage.query.filter_by(id=page_id, organization_id=organization_id).first_or_404()
    page.status = "closed"
    db.session.commit()
    try:
        from ngo_homesuite.db.utils import audit

        audit(
            "p2p.page.close",
            entity_type="p2p_page",
            entity_id=page.id,
            details={"organization_id": organization_id, "status": page.status},
        )
    except Exception:
        pass
    return page


def update_page(
    page_id: int,
    organization_id: int,
    *,
    title: Optional[str] = None,
    story: Optional[str] = None,
    goal_amount: Optional[float] = None,
) -> P2PPage:
    page = P2PPage.query.filter_by(id=page_id, organization_id=organization_id).first_or_404()
    if title is not None:
        page.title = title
    if story is not None:
        page.story = story
    if goal_amount is not None:
        page.goal_amount = goal_amount
    db.session.commit()
    return page


def get_page(page_id: int, organization_id: int) -> Optional[P2PPage]:
    return P2PPage.query.filter_by(id=page_id, organization_id=organization_id).first()


def get_page_by_slug(slug: str) -> Optional[P2PPage]:
    return P2PPage.query.filter_by(public_slug=slug).first()


def list_pages(
    organization_id: int,
    *,
    donor_id: Optional[int] = None,
    status: Optional[str] = None,
    campaign_slug: Optional[str] = None,
) -> List[P2PPage]:
    q = P2PPage.query.filter_by(organization_id=organization_id)
    if donor_id is not None:
        q = q.filter_by(donor_id=donor_id)
    if status:
        q = q.filter_by(status=status)
    if campaign_slug:
        q = q.filter_by(campaign_slug=campaign_slug)
    return q.order_by(P2PPage.created_at.desc()).all()


# ---------------------------------------------------------------------------
# Donation linking
# ---------------------------------------------------------------------------

def link_donation(page_id: int, organization_id: int, donation_id: int) -> P2PPageDonation:
    """Associate an existing donation record with a P2P page."""
    P2PPage.query.filter_by(id=page_id, organization_id=organization_id).first_or_404()
    donation = Donation.query.filter_by(id=donation_id, organization_id=organization_id).first()
    if donation is None:
        raise ValueError("invalid resource reference")

    existing = P2PPageDonation.query.filter_by(page_id=page_id, donation_id=donation_id).first()
    if existing:
        return existing
    link = P2PPageDonation(page_id=page_id, donation_id=donation_id)
    db.session.add(link)
    db.session.commit()
    try:
        from ngo_homesuite.db.utils import audit

        audit(
            "p2p.page.link_donation",
            entity_type="p2p_page",
            entity_id=page_id,
            details={
                "organization_id": organization_id,
                "donation_id": donation_id,
            },
        )
    except Exception:
        pass
    return link


def unlink_donation(page_id: int, donation_id: int) -> None:
    link = P2PPageDonation.query.filter_by(page_id=page_id, donation_id=donation_id).first()
    if link:
        db.session.delete(link)
        db.session.commit()


# ---------------------------------------------------------------------------
# Progress & reporting
# ---------------------------------------------------------------------------

def get_progress(page_id: int, organization_id: int) -> Dict[str, Any]:
    """Return current raised amount, donor count, and percentage of goal."""
    page = P2PPage.query.filter_by(id=page_id, organization_id=organization_id).first_or_404()

    links = P2PPageDonation.query.filter_by(page_id=page_id).all()
    donation_ids = [lnk.donation_id for lnk in links]

    if donation_ids:
        donations = Donation.query.filter(
            Donation.id.in_(donation_ids),
            Donation.organization_id == organization_id,
        ).all()
        total_raised = sum(d.amount for d in donations if d.amount)
        donor_ids = {d.donor_id for d in donations if d.donor_id}
    else:
        total_raised = 0.0
        donor_ids = set()

    pct = None
    if page.goal_amount and page.goal_amount > 0:
        pct = round(total_raised / page.goal_amount * 100, 1)

    return {
        "page_id": page.id,
        "title": page.title,
        "public_slug": page.public_slug,
        "status": page.status,
        "goal_amount": page.goal_amount,
        "total_raised": total_raised,
        "donor_count": len(donor_ids),
        "pct_of_goal": pct,
    }


def leaderboard(
    organization_id: int,
    campaign_slug: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return top P2P pages by amount raised."""
    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))

    q = (
        db.session.query(
            P2PPage.id.label("page_id"),
            P2PPage.title.label("title"),
            P2PPage.public_slug.label("slug"),
            func.coalesce(func.sum(Donation.amount), 0.0).label("raised"),
        )
        .outerjoin(P2PPageDonation, P2PPageDonation.page_id == P2PPage.id)
        .outerjoin(
            Donation,
            and_(
                Donation.id == P2PPageDonation.donation_id,
                Donation.organization_id == organization_id,
            ),
        )
        .filter(P2PPage.organization_id == organization_id, P2PPage.status == "active")
    )
    if campaign_slug:
        q = q.filter(P2PPage.campaign_slug == campaign_slug)

    rows = (
        q.group_by(P2PPage.id, P2PPage.title, P2PPage.public_slug)
        .order_by(func.coalesce(func.sum(Donation.amount), 0.0).desc(), P2PPage.id.asc())
        .offset(safe_offset)
        .limit(safe_limit)
        .all()
    )
    return [
        {
            "page_id": int(r.page_id),
            "title": str(r.title),
            "slug": str(r.slug),
            "raised": float(r.raised or 0.0),
        }
        for r in rows
    ]
