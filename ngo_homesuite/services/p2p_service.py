"""Peer-to-Peer Fundraising service.

Supports creating public fundraising pages, linking incoming donations,
tracking progress toward a goal, and listing active pages.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from werkzeug.exceptions import NotFound

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
    existing = db.session.scalars(select(P2PPage).where(P2PPage.public_slug == slug).limit(1)).first()
    if existing is None:
        return slug
    import secrets
    return f"{slug}-{secrets.token_urlsafe(4)}"


def _get_page_or_404(page_id: int, organization_id: int) -> P2PPage:
    page = db.session.scalars(
        select(P2PPage).where(P2PPage.id == page_id, P2PPage.organization_id == organization_id).limit(1)
    ).first()
    if page is None:
        raise NotFound()
    return page


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
    donor = db.session.scalars(
        select(Donor).where(Donor.id == donor_id, Donor.organization_id == organization_id).limit(1)
    ).first()
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
    page = _get_page_or_404(page_id, organization_id)
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
    page = _get_page_or_404(page_id, organization_id)
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
    page = _get_page_or_404(page_id, organization_id)
    if title is not None:
        page.title = title
    if story is not None:
        page.story = story
    if goal_amount is not None:
        page.goal_amount = goal_amount
    db.session.commit()
    return page


def get_page(page_id: int, organization_id: int) -> Optional[P2PPage]:
    return db.session.scalars(
        select(P2PPage).where(P2PPage.id == page_id, P2PPage.organization_id == organization_id).limit(1)
    ).first()


def get_page_by_slug(slug: str) -> Optional[P2PPage]:
    return db.session.scalars(select(P2PPage).where(P2PPage.public_slug == slug).limit(1)).first()


def list_pages(
    organization_id: int,
    *,
    donor_id: Optional[int] = None,
    status: Optional[str] = None,
    campaign_slug: Optional[str] = None,
) -> List[P2PPage]:
    stmt = select(P2PPage).where(P2PPage.organization_id == organization_id)
    if donor_id is not None:
        stmt = stmt.where(P2PPage.donor_id == donor_id)
    if status:
        stmt = stmt.where(P2PPage.status == status)
    if campaign_slug:
        stmt = stmt.where(P2PPage.campaign_slug == campaign_slug)
    stmt = stmt.order_by(P2PPage.created_at.desc())
    return list(db.session.scalars(stmt))


# ---------------------------------------------------------------------------
# Donation linking
# ---------------------------------------------------------------------------

def link_donation(page_id: int, organization_id: int, donation_id: int) -> P2PPageDonation:
    """Associate an existing donation record with a P2P page."""
    _get_page_or_404(page_id, organization_id)
    donation = db.session.scalars(
        select(Donation).where(Donation.id == donation_id, Donation.organization_id == organization_id).limit(1)
    ).first()
    if donation is None:
        raise ValueError("invalid resource reference")

    existing = db.session.scalars(
        select(P2PPageDonation)
        .where(P2PPageDonation.page_id == page_id, P2PPageDonation.donation_id == donation_id)
        .limit(1)
    ).first()
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


def unlink_donation(page_id: int, organization_id: int, donation_id: int) -> None:
    _get_page_or_404(page_id, organization_id)
    donation = db.session.scalars(
        select(Donation).where(Donation.id == donation_id, Donation.organization_id == organization_id).limit(1)
    ).first()
    if donation is None:
        raise ValueError("invalid resource reference")

    link = db.session.scalars(
        select(P2PPageDonation)
        .where(P2PPageDonation.page_id == page_id, P2PPageDonation.donation_id == donation_id)
        .limit(1)
    ).first()
    if link:
        db.session.delete(link)
        db.session.commit()


# ---------------------------------------------------------------------------
# Progress & reporting
# ---------------------------------------------------------------------------

def get_progress(page_id: int, organization_id: int) -> Dict[str, Any]:
    """Return current raised amount, donor count, and percentage of goal."""
    page = _get_page_or_404(page_id, organization_id)

    links = list(db.session.scalars(select(P2PPageDonation).where(P2PPageDonation.page_id == page_id)))
    donation_ids = [lnk.donation_id for lnk in links]

    if donation_ids:
        donations = list(
            db.session.scalars(
                select(Donation).where(
                    Donation.id.in_(donation_ids),
                    Donation.organization_id == organization_id,
                )
            )
        )
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

    stmt = select(P2PPage).where(P2PPage.organization_id == organization_id, P2PPage.status == "active")
    if campaign_slug:
        stmt = stmt.where(P2PPage.campaign_slug == campaign_slug)
    pages = list(db.session.scalars(stmt))
    if not pages:
        return []

    page_ids = [int(p.id) for p in pages if p.id is not None]
    links = list(db.session.scalars(select(P2PPageDonation).where(P2PPageDonation.page_id.in_(page_ids))))
    donation_ids = [int(link.donation_id) for link in links if link.donation_id is not None]
    donations_by_id: dict[int, Donation] = {}
    if donation_ids:
        donations = list(
            db.session.scalars(
                select(Donation).where(
                    Donation.id.in_(donation_ids),
                    Donation.organization_id == organization_id,
                )
            )
        )
        donations_by_id = {int(d.id): d for d in donations if d.id is not None}

    raised_by_page: dict[int, float] = {int(p.id): 0.0 for p in pages if p.id is not None}
    for link in links:
        if link.page_id is None or link.donation_id is None:
            continue
        donation = donations_by_id.get(int(link.donation_id))
        if donation is None:
            continue
        raised_by_page[int(link.page_id)] += float(donation.amount or 0.0)

    ranked_pages = sorted(
        [p for p in pages if p.id is not None],
        key=lambda p: (-raised_by_page.get(int(p.id), 0.0), int(p.id)),
    )
    sliced = ranked_pages[safe_offset:safe_offset + safe_limit]
    return [
        {
            "page_id": int(page.id),
            "title": str(page.title),
            "slug": str(page.public_slug),
            "raised": float(raised_by_page.get(int(page.id), 0.0)),
        }
        for page in sliced
    ]
