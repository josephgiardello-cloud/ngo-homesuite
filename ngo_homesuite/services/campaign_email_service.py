"""Campaign bulk email service.

Provides audience resolution, bulk dispatch, and campaign email analytics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from ngo_homesuite.models.core import (
    Campaign,
    CampaignEmailBatch,
    CampaignEmailDelivery,
    Donation,
    Donor,
    db,
)
from ngo_homesuite.utils.email import send_email


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _render_body(body_template: str, *, donor: Donor, campaign: Campaign) -> str:
    safe_name = (donor.name or "Supporter").strip() or "Supporter"
    return (
        str(body_template or "")
        .replace("{name}", safe_name)
        .replace("{campaign_name}", campaign.name)
    )


def _get_campaign_or_raise(campaign_id: int, organization_id: int) -> Campaign:
    campaign = db.session.scalars(
        select(Campaign).where(
            Campaign.id == int(campaign_id),
            Campaign.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if campaign is None:
        raise LookupError("Campaign not found")
    return campaign


def _resolve_recipients(
    organization_id: int,
    campaign_id: int,
    audience: dict[str, Any] | None,
) -> list[Donor]:
    audience = audience or {}

    stmt = select(Donor).where(
        Donor.organization_id == int(organization_id),
        Donor.email.is_not(None),
        func.length(func.trim(Donor.email)) > 0,
    )

    donor_ids = audience.get("donor_ids")
    if isinstance(donor_ids, list):
        parsed_ids = [int(x) for x in donor_ids if str(x).strip().isdigit()]
        if parsed_ids:
            stmt = stmt.where(Donor.id.in_(parsed_ids))

    if bool(audience.get("campaign_donors_only", False)):
        stmt = stmt.join(
            Donation,
            (Donation.donor_id == Donor.id)
            & (Donation.organization_id == Donor.organization_id)
            & (Donation.campaign_id == int(campaign_id)),
        )

    stmt = stmt.order_by(Donor.id.asc())
    recipients = list(db.session.scalars(stmt))

    deduped: dict[str, Donor] = {}
    for donor in recipients:
        email_key = str(donor.email or "").strip().lower()
        if email_key and email_key not in deduped:
            deduped[email_key] = donor
    return list(deduped.values())


def send_campaign_bulk_email(
    organization_id: int,
    campaign_id: int,
    *,
    created_by_user_id: int | None,
    subject: str,
    body: str,
    audience: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    subject_value = str(subject or "").strip()
    body_value = str(body or "").strip()
    if not subject_value:
        raise ValueError("subject is required")
    if not body_value:
        raise ValueError("body is required")

    campaign = _get_campaign_or_raise(campaign_id, organization_id)
    recipients = _resolve_recipients(organization_id, campaign.id, audience)

    if bool(dry_run):
        return {
            "dry_run": True,
            "campaign_id": int(campaign.id),
            "total_recipients": len(recipients),
            "sample_emails": [str(r.email) for r in recipients[:20]],
        }

    batch = CampaignEmailBatch(
        organization_id=int(organization_id),
        campaign_id=int(campaign.id),
        created_by_user_id=int(created_by_user_id) if created_by_user_id is not None else None,
        subject=subject_value,
        body=body_value,
        audience_json=audience or {},
        status="queued",
        total_recipients=len(recipients),
        sent_count=0,
        failed_count=0,
    )
    db.session.add(batch)
    db.session.flush()

    sent = 0
    failed = 0
    for donor in recipients:
        recipient_email = str(donor.email or "").strip()
        rendered = _render_body(body_value, donor=donor, campaign=campaign)
        ok = bool(
            send_email(
                to=recipient_email,
                subject=subject_value,
                context={"text": rendered},
            )
        )

        if ok:
            sent += 1
            delivery_status = "sent"
            error_message = None
            sent_at = _utcnow()
        else:
            failed += 1
            delivery_status = "failed"
            error_message = "delivery failed"
            sent_at = None

        db.session.add(
            CampaignEmailDelivery(
                batch_id=int(batch.id),
                organization_id=int(organization_id),
                campaign_id=int(campaign.id),
                donor_id=int(donor.id),
                recipient_email=recipient_email,
                delivery_status=delivery_status,
                error_message=error_message,
                sent_at=sent_at,
            )
        )

    batch.sent_count = sent
    batch.failed_count = failed
    batch.sent_at = _utcnow()
    if sent == 0 and failed > 0:
        batch.status = "failed"
    elif sent > 0 and failed > 0:
        batch.status = "partial_failed"
    else:
        batch.status = "sent"

    db.session.commit()

    return {
        "dry_run": False,
        "batch_id": int(batch.id),
        "campaign_id": int(campaign.id),
        "status": batch.status,
        "total_recipients": int(batch.total_recipients),
        "sent": int(batch.sent_count),
        "failed": int(batch.failed_count),
    }


def campaign_email_analytics(organization_id: int, campaign_id: int) -> dict[str, Any]:
    campaign = _get_campaign_or_raise(campaign_id, organization_id)

    totals = db.session.execute(
        select(
            func.count(CampaignEmailBatch.id),
            func.coalesce(func.sum(CampaignEmailBatch.total_recipients), 0),
            func.coalesce(func.sum(CampaignEmailBatch.sent_count), 0),
            func.coalesce(func.sum(CampaignEmailBatch.failed_count), 0),
        ).where(
            CampaignEmailBatch.organization_id == int(organization_id),
            CampaignEmailBatch.campaign_id == int(campaign.id),
        )
    ).one()

    recent_batches = list(
        db.session.scalars(
            select(CampaignEmailBatch).where(
                CampaignEmailBatch.organization_id == int(organization_id),
                CampaignEmailBatch.campaign_id == int(campaign.id),
            ).order_by(CampaignEmailBatch.created_at.desc()).limit(10)
        )
    )

    return {
        "campaign_id": int(campaign.id),
        "campaign_name": campaign.name,
        "batch_count": int(totals[0] or 0),
        "total_recipients": int(totals[1] or 0),
        "total_sent": int(totals[2] or 0),
        "total_failed": int(totals[3] or 0),
        "recent_batches": [
            {
                "id": int(b.id),
                "status": b.status,
                "total_recipients": int(b.total_recipients),
                "sent_count": int(b.sent_count),
                "failed_count": int(b.failed_count),
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "sent_at": b.sent_at.isoformat() if b.sent_at else None,
            }
            for b in recent_batches
        ],
    }
