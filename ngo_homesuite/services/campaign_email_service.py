"""Campaign bulk email service.

Provides audience resolution, bulk dispatch, and campaign email analytics.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
import json
import re
import time
from typing import Any
from urllib.parse import quote_plus

from flask import current_app, has_app_context
from sqlalchemy import case, func, select, text

from ngo_homesuite.policy import enforce_error_contract
from ngo_homesuite.models.core import (
    Campaign,
    CampaignEmailBatch,
    CampaignEmailDelivery,
    CampaignEmailOptOut,
    Donation,
    Donor,
    ExternalCommunicationAuthorization,
    db,
)
from ngo_homesuite.utils.email import send_email


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _render_body(body_template: str, *, donor: Donor, campaign: Campaign) -> str:
    safe_name = (donor.name or "Supporter").strip() or "Supporter"
    return (
        str(body_template or "")
        .replace("{name}", safe_name)
        .replace("{campaign_name}", campaign.name)
    )


def _quality_hints(subject: str, body: str) -> list[str]:
    hints: list[str] = []
    if len(subject.strip()) < 8:
        hints.append("Subject line is very short; aim for at least 8 characters.")
    if len(subject.strip()) > 90:
        hints.append("Subject line is long; deliverability may improve below 90 characters.")
    if "{name}" not in body:
        hints.append("Body does not include {name}; personalization may improve donor response.")
    if len(body.strip()) < 80:
        hints.append("Body is brief; consider adding impact context and a clear CTA.")
    lower = body.lower()
    if "donate" not in lower and "support" not in lower and "give" not in lower:
        hints.append("CTA appears weak; include a clear donate/support action.")
    return hints


def _tracking_base_url() -> str:
    base = str(current_app.config.get("PUBLIC_APP_URL") or "").strip() if has_app_context() else ""
    if not base:
        base = "http://localhost:5000"
    return base.rstrip("/")


def _tracking_secret() -> bytes:
    secret = str(current_app.config.get("TRACKING_SIGNING_SECRET") or current_app.config.get("SECRET_KEY") or "").strip()
    if not secret:
        secret = "ngohs-tracking-default"
    return secret.encode("utf-8")


def _tracking_signature(
    *,
    kind: str,
    campaign_id: int,
    donor_id: int,
    delivery_id: int,
    issued_at: int,
    target_url: str = "",
) -> str:
    payload = "|".join([
        str(kind),
        str(int(campaign_id)),
        str(int(donor_id)),
        str(int(delivery_id)),
        str(int(issued_at)),
        str(target_url or ""),
    ])
    digest = hmac.new(_tracking_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def _unsub_signature(*, email: str, donor_id: int, campaign_id: int, issued_at: int) -> str:
    payload = "|".join(["unsub", str(email).lower(), str(int(donor_id)), str(int(campaign_id)), str(int(issued_at))])
    return hmac.new(_tracking_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_unsub_signature(
    *,
    email: str,
    donor_id: int,
    campaign_id: int,
    issued_at: int,
    signature: str,
    max_age_seconds: int = 2592000,
) -> bool:
    """Verify an unsubscribe link signature (default 30-day TTL)."""
    now_ts = int(time.time())
    if issued_at <= 0 or issued_at > now_ts:
        return False
    if now_ts - int(issued_at) > int(max(1, max_age_seconds)):
        return False
    expected = _unsub_signature(
        email=email,
        donor_id=donor_id,
        campaign_id=campaign_id,
        issued_at=issued_at,
    )
    return hmac.compare_digest(str(signature or ""), expected)


def verify_tracking_signature(
    *,
    kind: str,
    campaign_id: int,
    donor_id: int,
    delivery_id: int,
    issued_at: int,
    signature: str,
    target_url: str = "",
    max_age_seconds: int = 604800,
) -> bool:
    now_ts = int(time.time())
    if issued_at <= 0 or issued_at > now_ts:
        return False
    if now_ts - int(issued_at) > int(max(1, max_age_seconds)):
        return False
    expected = _tracking_signature(
        kind=kind,
        campaign_id=campaign_id,
        donor_id=donor_id,
        delivery_id=delivery_id,
        issued_at=issued_at,
        target_url=target_url,
    )
    return hmac.compare_digest(str(signature or ""), expected)


def _with_tracking_links(
    body: str,
    *,
    campaign_id: int,
    donor_id: int,
    delivery_id: int,
    recipient_email: str = "",
) -> str:
    base = _tracking_base_url()

    def _replace_href(match: re.Match[str]) -> str:
        quote = match.group(1)
        target = match.group(2)
        issued_at = int(time.time())
        signature = _tracking_signature(
            kind="click",
            campaign_id=int(campaign_id),
            donor_id=int(donor_id),
            delivery_id=int(delivery_id),
            issued_at=issued_at,
            target_url=target,
        )
        tracked = (
            f"{base}/api/v2/campaigns/email/click?campaign_id={int(campaign_id)}"
            f"&donor_id={int(donor_id)}&delivery_id={int(delivery_id)}"
            f"&ts={issued_at}&sig={signature}&url={quote_plus(target)}"
        )
        return f"href={quote}{tracked}{quote}"

    tracked_body = re.sub(r"href=(['\"])([^'\"]+)\1", _replace_href, body or "")

    # Open-tracking pixel
    open_ts = int(time.time())
    open_sig = _tracking_signature(
        kind="open",
        campaign_id=int(campaign_id),
        donor_id=int(donor_id),
        delivery_id=int(delivery_id),
        issued_at=open_ts,
    )
    pixel_url = (
        f"{base}/api/v2/campaigns/email/open-pixel?campaign_id={int(campaign_id)}"
        f"&donor_id={int(donor_id)}&delivery_id={int(delivery_id)}"
        f"&ts={open_ts}&sig={open_sig}"
    )
    pixel_html = f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none;"/>'

    # CAN-SPAM / unsubscribe footer
    footer = ""
    if recipient_email:
        unsub_ts = int(time.time())
        unsub_sig = _unsub_signature(
            email=str(recipient_email).lower(),
            donor_id=int(donor_id),
            campaign_id=int(campaign_id),
            issued_at=unsub_ts,
        )
        unsub_url = (
            f"{base}/api/v2/campaigns/email/unsubscribe"
            f"?email={quote_plus(str(recipient_email).lower())}"
            f"&donor_id={int(donor_id)}&campaign_id={int(campaign_id)}"
            f"&ts={unsub_ts}&sig={unsub_sig}"
        )
        footer = (
            "\n\n<div style=\"margin-top:2em;padding-top:1em;border-top:1px solid #e0e0e0;"
            "font-size:12px;color:#999;text-align:center;font-family:Arial,sans-serif;\">"
            "<p style=\"margin:0 0 4px;\">You are receiving this email as a valued supporter.</p>"
            f"<p style=\"margin:0;\"><a href=\"{unsub_url}\" "
            "style=\"color:#999;text-decoration:underline;\">Unsubscribe</a> "
            "from campaign emails.</p></div>"
        )

    return f"{tracked_body}\n\n{pixel_html}{footer}"


def _email_rate_cap_settings() -> tuple[int, int, float]:
    max_per_window = int(current_app.config.get("CAMPAIGN_EMAIL_MAX_PER_MINUTE", 240) or 0)
    max_per_domain_per_window = int(current_app.config.get("CAMPAIGN_EMAIL_MAX_PER_DOMAIN_PER_MINUTE", 120) or 0)
    window_seconds = float(current_app.config.get("CAMPAIGN_EMAIL_RATE_WINDOW_SECONDS", 60.0) or 60.0)
    return max(0, max_per_window), max(0, max_per_domain_per_window), max(1.0, window_seconds)


def _recipient_domain(recipient_email: str) -> str:
    email = str(recipient_email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1]


def _prune_window(queue: deque[float], *, cutoff: float) -> None:
    while queue and queue[0] <= cutoff:
        queue.popleft()


def _enforce_send_rate_caps(
    *,
    recipient_email: str,
    sent_at_times: deque[float],
    sent_at_times_by_domain: dict[str, deque[float]],
    max_per_window: int,
    max_per_domain_per_window: int,
    window_seconds: float,
) -> bool:
    if max_per_window <= 0 and max_per_domain_per_window <= 0:
        return True

    domain = _recipient_domain(recipient_email)
    domain_queue = sent_at_times_by_domain[domain] if domain else deque()
    now = time.monotonic()
    cutoff = now - window_seconds
    _prune_window(sent_at_times, cutoff=cutoff)
    if domain:
        _prune_window(domain_queue, cutoff=cutoff)

    if max_per_window > 0 and len(sent_at_times) >= max_per_window:
        return False
    if domain and max_per_domain_per_window > 0 and len(domain_queue) >= max_per_domain_per_window:
        return False

    stamp = time.monotonic()
    sent_at_times.append(stamp)
    if domain:
        domain_queue.append(stamp)
    return True


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


def _donor_giving_metrics(organization_id: int, donor_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not donor_ids:
        return {}

    completed_statuses = ("received", "processed", "receipted")
    rows = db.session.execute(
        select(
            Donation.donor_id,
            func.coalesce(func.sum(Donation.amount), 0.0),
            func.count(Donation.id),
            func.max(Donation.donation_date),
        ).where(
            Donation.organization_id == int(organization_id),
            Donation.donor_id.in_(donor_ids),
            Donation.status.in_(completed_statuses),
        ).group_by(Donation.donor_id)
    ).all()

    metrics: dict[int, dict[str, Any]] = {}
    for donor_id, total_given, gift_count, last_gift_at in rows:
        metrics[int(donor_id)] = {
            "total_given": float(total_given or 0.0),
            "gift_count": int(gift_count or 0),
            "last_gift_at": last_gift_at,
        }
    return metrics


def _resolve_recipients(
    organization_id: int,
    campaign_id: int,
    audience: dict[str, Any] | None,
) -> list[Donor]:
    audience = audience or {}

    smart_group_id_raw = audience.get("smart_group_id")
    smart_group_member_ids: list[int] | None = None
    if smart_group_id_raw is not None:
        try:
            smart_group_id = int(smart_group_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("smart_group_id must be an integer") from exc
        if smart_group_id <= 0:
            raise ValueError("smart_group_id must be a positive integer")

        from ngo_homesuite.services.smart_groups_service import evaluate_group

        try:
            members = evaluate_group(smart_group_id, int(organization_id))
        except Exception as exc:
            raise ValueError("smart_group_id not found for this organization") from exc

        smart_group_member_ids = [
            int(item.get("donor_id"))
            for item in members
            if str(item.get("donor_id") or "").strip().isdigit()
        ]
        if not smart_group_member_ids:
            return []

    stmt = select(Donor).where(
        Donor.organization_id == int(organization_id),
        Donor.email.is_not(None),
        func.length(func.trim(Donor.email)) > 0,
    )

    if smart_group_member_ids is not None:
        stmt = stmt.where(Donor.id.in_(smart_group_member_ids))

    donor_ids = audience.get("donor_ids")
    if isinstance(donor_ids, list):
        parsed_ids = [int(x) for x in donor_ids if str(x).strip().isdigit()]
        if parsed_ids:
            stmt = stmt.where(Donor.id.in_(parsed_ids))

    donor_type = str(audience.get("donor_type") or "").strip().lower()
    if donor_type:
        stmt = stmt.where(func.lower(Donor.donor_type) == donor_type)

    stmt = stmt.order_by(Donor.id.asc())
    base_recipients = list(db.session.scalars(stmt))

    donor_id_list = [int(d.id) for d in base_recipients]
    metrics = _donor_giving_metrics(int(organization_id), donor_id_list)

    completed_statuses = ("received", "processed", "receipted")
    campaign_rows = db.session.execute(
        select(
            Donation.donor_id,
            func.coalesce(func.sum(Donation.amount), 0.0),
        ).where(
            Donation.organization_id == int(organization_id),
            Donation.campaign_id == int(campaign_id),
            Donation.donor_id.in_(donor_id_list if donor_id_list else [-1]),
            Donation.status.in_(completed_statuses),
        ).group_by(Donation.donor_id)
    ).all()
    campaign_totals = {int(donor_id): float(total or 0.0) for donor_id, total in campaign_rows}

    now = _utcnow()
    min_total_given = float(audience.get("min_total_given") or 0.0)
    campaign_donors_only = bool(audience.get("campaign_donors_only", False))
    gifted_within_days = int(audience.get("gifted_within_days") or 0)
    lapsed_days_min = int(audience.get("lapsed_days_min") or 0)

    recipients: list[Donor] = []
    for donor in base_recipients:
        donor_metrics = metrics.get(int(donor.id), {"total_given": 0.0, "gift_count": 0, "last_gift_at": None})
        if float(donor_metrics.get("total_given", 0.0)) < min_total_given:
            continue
        if campaign_donors_only and float(campaign_totals.get(int(donor.id), 0.0)) <= 0.0:
            continue

        last_gift_at = donor_metrics.get("last_gift_at")
        if gifted_within_days > 0:
            if not last_gift_at:
                continue
            if (now - last_gift_at).days > gifted_within_days:
                continue
        if lapsed_days_min > 0:
            if not last_gift_at:
                # No giving history counts as lapsed for this audience mode.
                pass
            elif (now - last_gift_at).days < lapsed_days_min:
                continue

        recipients.append(donor)

    top_n_by_total_given = int(audience.get("top_n_by_total_given") or 0)
    if top_n_by_total_given > 0:
        recipients.sort(key=lambda d: float(metrics.get(int(d.id), {}).get("total_given", 0.0)), reverse=True)
        recipients = recipients[:top_n_by_total_given]
    else:
        recipients.sort(key=lambda d: int(d.id))

    deduped: dict[str, Donor] = {}
    for donor in recipients:
        email_key = str(donor.email or "").strip().lower()
        if email_key and email_key not in deduped:
            deduped[email_key] = donor

    # Exclude globally opted-out / unsubscribed email addresses
    try:
        opted_out = set(
            str(row).strip().lower()
            for (row,) in db.session.execute(
                select(CampaignEmailOptOut.email).where(
                    CampaignEmailOptOut.organization_id == int(organization_id)
                )
            ).all()
            if row
        )
        if opted_out:
            deduped = {k: v for k, v in deduped.items() if k not in opted_out}
    except Exception as exc:
        logger.warning(
            "Unable to apply campaign email opt-out suppression for organization_id=%s: %s",
            int(organization_id),
            exc,
        )

    # Exclude global suppression-list addresses (bounces, complaints, spam reports).
    # This table is maintained by provider webhook handlers.
    try:
        suppressed = set(
            str(row).strip().lower()
            for (row,) in db.session.execute(
                text("SELECT email FROM email_suppressions")
            ).all()
            if row
        )
        if suppressed:
            deduped = {k: v for k, v in deduped.items() if k not in suppressed}
    except Exception as exc:
        logger.warning(
            "Unable to apply global suppression list for organization_id=%s: %s",
            int(organization_id),
            exc,
        )

    return list(deduped.values())


def preview_campaign_email(
    organization_id: int,
    campaign_id: int,
    *,
    subject: str,
    body: str,
    audience: dict[str, Any] | None = None,
) -> dict[str, Any]:
    campaign = _get_campaign_or_raise(campaign_id, organization_id)
    recipients = _resolve_recipients(organization_id, campaign.id, audience)

    previews = [
        {
            "donor_id": int(d.id),
            "donor_name": d.name,
            "recipient_email": d.email,
            "subject": str(subject),
            "body": _render_body(str(body), donor=d, campaign=campaign),
        }
        for d in recipients[:5]
    ]

    by_type: dict[str, int] = {}
    for donor in recipients:
        key = str(donor.donor_type or "unknown").strip().lower() or "unknown"
        by_type[key] = by_type.get(key, 0) + 1

    return {
        "campaign_id": int(campaign.id),
        "campaign_name": campaign.name,
        "total_recipients": len(recipients),
        "segment_breakdown": by_type,
        "quality_hints": _quality_hints(str(subject), str(body)),
        "sample_preview": previews,
    }


def generate_ai_campaign_email_draft(
    organization_id: int,
    campaign_id: int,
    *,
    objective: str | None = None,
    tone: str | None = None,
    audience: dict[str, Any] | None = None,
    ask_amount: float | None = None,
) -> dict[str, Any]:
    campaign = _get_campaign_or_raise(campaign_id, organization_id)
    recipients = _resolve_recipients(organization_id, campaign.id, audience)

    objective_value = str(objective or "increase donor participation").strip() or "increase donor participation"
    tone_value = str(tone or "warm and confident").strip() or "warm and confident"
    ask_value = float(ask_amount or max(50.0, round(float(campaign.goal_amount or 0.0) * 0.02, 2)))

    sample_names = ", ".join([str(d.name) for d in recipients[:3]]) or "Supporters"
    prompt = (
        "Write a nonprofit fundraising campaign email draft as JSON with keys subject and body. "
        "Use placeholders {name} and {campaign_name}. Keep body under 180 words. "
        f"Campaign: {campaign.name}. Objective: {objective_value}. Tone: {tone_value}. "
        f"Estimated audience size: {len(recipients)}. Example supporters: {sample_names}. "
        f"Target ask amount: ${ask_value:,.2f}."
    )

    fallback_subject = f"{campaign.name}: your support can power the next milestone"
    fallback_body = (
        "Hi {name},\n\n"
        "Thank you for standing with us. We are building momentum in {campaign_name}, "
        f"and a gift of ${ask_value:,.2f} would help us move this work forward right now.\n\n"
        "Your support directly strengthens services for families in our community. "
        "If you are able, please consider making your contribution this week.\n\n"
        "With gratitude,\n"
        "Fundraising Team"
    )

    try:
        from ngo_homesuite.ai.apex_client import ApexClient

        host = current_app.config.get("OLLAMA_HOST", "http://localhost:11434") if has_app_context() else "http://localhost:11434"
        model = current_app.config.get("OLLAMA_MODEL", "llama3.2") if has_app_context() else "llama3.2"
        timeout_s = float(current_app.config.get("OLLAMA_TIMEOUT_S", 45.0)) if has_app_context() else 45.0

        client = ApexClient(host=str(host), model=str(model), timeout_s=timeout_s)
        raw = client.query(
            prompt=prompt,
            model=str(model),
            system_prompt="You are an expert nonprofit email strategist. Return strict JSON only.",
        )

        parsed = json.loads(raw)
        subject_value = str(parsed.get("subject") or fallback_subject).strip() or fallback_subject
        body_value = str(parsed.get("body") or fallback_body).strip() or fallback_body
        generated_by = "ai"
    except Exception:
        subject_value = fallback_subject
        body_value = fallback_body
        generated_by = "fallback"

    return {
        "campaign_id": int(campaign.id),
        "campaign_name": campaign.name,
        "generated_by": generated_by,
        "objective": objective_value,
        "tone": tone_value,
        "ask_amount": round(ask_value, 2),
        "subject": subject_value,
        "body": body_value,
        "quality_hints": _quality_hints(subject_value, body_value),
        "audience_size_estimate": len(recipients),
    }


@enforce_error_contract
def send_campaign_bulk_email(
    organization_id: int,
    campaign_id: int,
    *,
    created_by_user_id: int | None,
    created_by_username: str | None,
    created_by_role: str | None,
    subject: str,
    body: str,
    audience: dict[str, Any] | None = None,
    human_authorization: dict[str, Any] | None = None,
    dry_run: bool = False,
    scheduled_at: datetime | None = None,
) -> dict[str, Any]:
    subject_value = str(subject or "").strip()
    body_value = str(body or "").strip()
    if not subject_value:
        raise ValueError("subject is required")
    if not body_value:
        raise ValueError("body is required")

    authorization = human_authorization if isinstance(human_authorization, dict) else {}
    if created_by_user_id is None or int(created_by_user_id) <= 0:
        raise ValueError("authorized user id is required for outbound external communication")
    reviewer_name = str(authorization.get("reviewer_name") or "").strip()
    warning_acknowledged = bool(authorization.get("warning_acknowledged", False))
    confirmation_phrase = str(authorization.get("human_confirmation_text") or "").strip()
    required_phrase = "I CONFIRM HUMAN REVIEW"
    if not reviewer_name:
        raise ValueError("reviewer_name is required for outbound external communication authorization")
    if not warning_acknowledged:
        raise ValueError("warning acknowledgement is required for outbound external communication authorization")
    if confirmation_phrase != required_phrase:
        raise ValueError(f"confirmation phrase must match '{required_phrase}'")

    campaign = _get_campaign_or_raise(campaign_id, organization_id)
    recipients = _resolve_recipients(organization_id, campaign.id, audience)

    if bool(dry_run):
        return {
            "dry_run": True,
            "campaign_id": int(campaign.id),
            "total_recipients": len(recipients),
            "sample_emails": [str(r.email) for r in recipients[:20]],
        }

    authorization_audit = ExternalCommunicationAuthorization(
        organization_id=int(organization_id),
        user_id=int(created_by_user_id),
        username=str(created_by_username or "unknown"),
        user_role=str(created_by_role or "unknown"),
        channel="email",
        communication_type="campaign_bulk_email",
        campaign_id=int(campaign.id),
        warning_acknowledged=True,
        confirmation_phrase=confirmation_phrase,
        reviewer_name=reviewer_name,
        reviewer_role=str(authorization.get("reviewer_role") or "").strip() or None,
        details_json={
            "subject": subject_value,
            "contains_internal_details": bool(authorization.get("contains_internal_details", False)),
            "ai_assisted": bool(authorization.get("ai_assisted", False)),
            "requested_recipients": len(recipients),
        },
    )
    db.session.add(authorization_audit)
    db.session.flush()

    now_dt = _utcnow()
    is_scheduled = bool(scheduled_at and isinstance(scheduled_at, datetime) and scheduled_at > now_dt)
    batch = CampaignEmailBatch(
        organization_id=int(organization_id),
        campaign_id=int(campaign.id),
        created_by_user_id=int(created_by_user_id) if created_by_user_id is not None else None,
        subject=subject_value,
        body=body_value,
        audience_json=audience or {},
        status="scheduled" if is_scheduled else "queued",
        total_recipients=len(recipients),
        sent_count=0,
        failed_count=0,
        scheduled_at=scheduled_at if is_scheduled else None,
    )
    db.session.add(batch)
    db.session.flush()
    authorization_audit.batch_id = int(batch.id)

    if is_scheduled:
        db.session.commit()
        return {
            "dry_run": False,
            "scheduled": True,
            "batch_id": int(batch.id),
            "authorization_audit_id": int(authorization_audit.id),
            "campaign_id": int(campaign.id),
            "status": "scheduled",
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
            "total_recipients": int(batch.total_recipients),
        }

    sent = 0
    failed = 0
    max_per_window, max_per_domain_per_window, window_seconds = _email_rate_cap_settings()
    sent_at_times: deque[float] = deque()
    sent_at_times_by_domain: dict[str, deque[float]] = defaultdict(deque)
    for donor in recipients:
        recipient_email = str(donor.email or "").strip()
        within_cap = _enforce_send_rate_caps(
            recipient_email=recipient_email,
            sent_at_times=sent_at_times,
            sent_at_times_by_domain=sent_at_times_by_domain,
            max_per_window=max_per_window,
            max_per_domain_per_window=max_per_domain_per_window,
            window_seconds=window_seconds,
        )
        with db.session.begin_nested():
            delivery = CampaignEmailDelivery(
                batch_id=int(batch.id),
                organization_id=int(organization_id),
                campaign_id=int(campaign.id),
                donor_id=int(donor.id),
                recipient_email=recipient_email,
                delivery_status="pending",
            )
            db.session.add(delivery)
            db.session.flush()

            if not within_cap:
                failed += 1
                delivery.delivery_status = "failed"
                delivery.error_message = "rate_limited"
                delivery.sent_at = None
                continue

            try:
                rendered = _render_body(body_value, donor=donor, campaign=campaign)
                rendered = _with_tracking_links(
                    rendered,
                    campaign_id=int(campaign.id),
                    donor_id=int(donor.id),
                    delivery_id=int(delivery.id),
                    recipient_email=recipient_email,
                )
                ok = bool(
                    send_email(
                        to=recipient_email,
                        subject=subject_value,
                        context={"text": rendered},
                    )
                )
            except Exception as exc:
                ok = False
                delivery.error_message = f"delivery_exception:{exc.__class__.__name__}"

            if ok:
                sent += 1
                delivery.delivery_status = "sent"
                delivery.error_message = None
                delivery.sent_at = _utcnow()
            else:
                failed += 1
                delivery.delivery_status = "failed"
                if not delivery.error_message:
                    delivery.error_message = "delivery failed"
                delivery.sent_at = None

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
        "authorization_audit_id": int(authorization_audit.id),
        "authorized_at": authorization_audit.authorized_at.isoformat() if authorization_audit.authorized_at else None,
        "campaign_id": int(campaign.id),
        "status": batch.status,
        "total_recipients": int(batch.total_recipients),
        "sent": int(batch.sent_count),
        "failed": int(batch.failed_count),
    }


@enforce_error_contract
def process_scheduled_campaign_email_batches(*, limit: int = 100, now: datetime | None = None) -> dict[str, Any]:
    """Process due campaign email batches created with scheduled_at in the future."""
    now_dt = now if isinstance(now, datetime) else _utcnow()
    batch_limit = max(1, min(int(limit), 500))

    due_batches = list(
        db.session.scalars(
            select(CampaignEmailBatch).where(
                CampaignEmailBatch.status == "scheduled",
                CampaignEmailBatch.scheduled_at.is_not(None),
                CampaignEmailBatch.scheduled_at <= now_dt,
            ).order_by(
                CampaignEmailBatch.scheduled_at.asc(),
                CampaignEmailBatch.id.asc(),
            ).limit(batch_limit)
        )
    )

    processed = 0
    sent_batches = 0
    failed_batches = 0
    total_sent = 0
    total_failed = 0

    for batch in due_batches:
        processed += 1
        try:
            campaign = db.session.get(Campaign, int(batch.campaign_id))
            if campaign is None or int(campaign.organization_id) != int(batch.organization_id):
                batch.status = "failed"
                batch.sent_count = 0
                batch.failed_count = int(batch.total_recipients or 0)
                batch.sent_at = _utcnow()
                db.session.commit()
                failed_batches += 1
                total_failed += int(batch.failed_count)
                continue

            recipients = _resolve_recipients(int(batch.organization_id), int(batch.campaign_id), batch.audience_json or {})
            sent = 0
            failed = 0
            max_per_window, max_per_domain_per_window, window_seconds = _email_rate_cap_settings()
            sent_at_times: deque[float] = deque()
            sent_at_times_by_domain: dict[str, deque[float]] = defaultdict(deque)

            for donor in recipients:
                recipient_email = str(donor.email or "").strip()
                within_cap = _enforce_send_rate_caps(
                    recipient_email=recipient_email,
                    sent_at_times=sent_at_times,
                    sent_at_times_by_domain=sent_at_times_by_domain,
                    max_per_window=max_per_window,
                    max_per_domain_per_window=max_per_domain_per_window,
                    window_seconds=window_seconds,
                )
                with db.session.begin_nested():
                    delivery = CampaignEmailDelivery(
                        batch_id=int(batch.id),
                        organization_id=int(batch.organization_id),
                        campaign_id=int(batch.campaign_id),
                        donor_id=int(donor.id),
                        recipient_email=recipient_email,
                        delivery_status="pending",
                    )
                    db.session.add(delivery)
                    db.session.flush()

                    if not within_cap:
                        failed += 1
                        delivery.delivery_status = "failed"
                        delivery.error_message = "rate_limited"
                        delivery.sent_at = None
                        continue

                    try:
                        rendered = _render_body(str(batch.body), donor=donor, campaign=campaign)
                        rendered = _with_tracking_links(
                            rendered,
                            campaign_id=int(campaign.id),
                            donor_id=int(donor.id),
                            delivery_id=int(delivery.id),
                            recipient_email=recipient_email,
                        )
                        ok = bool(
                            send_email(
                                to=recipient_email,
                                subject=str(batch.subject),
                                context={"text": rendered},
                            )
                        )
                    except Exception as exc:
                        ok = False
                        delivery.error_message = f"delivery_exception:{exc.__class__.__name__}"

                    if ok:
                        sent += 1
                        delivery.delivery_status = "sent"
                        delivery.error_message = None
                        delivery.sent_at = _utcnow()
                    else:
                        failed += 1
                        delivery.delivery_status = "failed"
                        if not delivery.error_message:
                            delivery.error_message = "delivery failed"
                        delivery.sent_at = None

            batch.sent_count = int(sent)
            batch.failed_count = int(failed)
            batch.sent_at = _utcnow()
            if sent == 0 and failed > 0:
                batch.status = "failed"
            elif sent > 0 and failed > 0:
                batch.status = "partial_failed"
            else:
                batch.status = "sent"

            db.session.commit()

            if batch.status == "failed":
                failed_batches += 1
            else:
                sent_batches += 1
            total_sent += int(batch.sent_count)
            total_failed += int(batch.failed_count)
        except Exception:
            db.session.rollback()
            failed_batches += 1
            logger.exception("Scheduled campaign email batch processing failed for batch_id=%s", int(batch.id))

            failed_batch = db.session.get(CampaignEmailBatch, int(batch.id))
            if failed_batch is not None:
                failed_batch.status = "failed"
                failed_batch.sent_count = 0
                failed_batch.failed_count = int(failed_batch.total_recipients or 0)
                failed_batch.sent_at = _utcnow()
                db.session.commit()
                total_failed += int(failed_batch.failed_count)

    return {
        "processed_batches": int(processed),
        "sent_batches": int(sent_batches),
        "failed_batches": int(failed_batches),
        "emails_sent": int(total_sent),
        "emails_failed": int(total_failed),
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

    engagement_totals = db.session.execute(
        select(
            func.coalesce(func.sum(CampaignEmailDelivery.open_count), 0),
            func.coalesce(func.sum(CampaignEmailDelivery.click_count), 0),
        ).where(
            CampaignEmailDelivery.organization_id == int(organization_id),
            CampaignEmailDelivery.campaign_id == int(campaign.id),
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

    opt_out_counts = db.session.execute(
        select(
            func.count(CampaignEmailOptOut.id),
            func.coalesce(
                func.sum(
                    case((CampaignEmailOptOut.campaign_id == int(campaign.id), 1), else_=0)
                ),
                0,
            ),
        ).where(
            CampaignEmailOptOut.organization_id == int(organization_id),
        )
    ).one()

    suppression_total = 0
    suppression_reason_breakdown: dict[str, int] = {}
    try:
        suppression_total_row = db.session.execute(
            text("SELECT COUNT(*) FROM email_suppressions")
        ).one()
        suppression_total = int((suppression_total_row[0] if suppression_total_row else 0) or 0)

        suppression_reason_rows = db.session.execute(
            text(
                """
                SELECT COALESCE(NULLIF(TRIM(reason), ''), 'unknown') AS reason, COUNT(*) AS count
                FROM email_suppressions
                GROUP BY COALESCE(NULLIF(TRIM(reason), ''), 'unknown')
                """
            )
        ).all()
        suppression_reason_breakdown = {
            str(reason): int(count or 0)
            for reason, count in suppression_reason_rows
        }
    except Exception as exc:
        logger.warning("Campaign analytics suppression summary unavailable: %s", exc)

    return {
        "campaign_id": int(campaign.id),
        "campaign_name": campaign.name,
        "batch_count": int(totals[0] or 0),
        "total_recipients": int(totals[1] or 0),
        "total_sent": int(totals[2] or 0),
        "total_failed": int(totals[3] or 0),
        "total_opens": int(engagement_totals[0] or 0),
        "total_clicks": int(engagement_totals[1] or 0),
        "opt_out_count": int(opt_out_counts[0] or 0),
        "campaign_opt_out_count": int(opt_out_counts[1] or 0),
        "suppression_count": int(suppression_total),
        "suppression_reason_breakdown": suppression_reason_breakdown,
        "recent_batches": [
            {
                "id": int(b.id),
                "status": b.status,
                "total_recipients": int(b.total_recipients),
                "sent_count": int(b.sent_count),
                "failed_count": int(b.failed_count),
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "sent_at": b.sent_at.isoformat() if b.sent_at else None,
                "scheduled_at": b.scheduled_at.isoformat() if b.scheduled_at else None,
            }
            for b in recent_batches
        ],
    }
