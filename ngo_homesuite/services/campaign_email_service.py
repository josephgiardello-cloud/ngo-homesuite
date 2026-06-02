"""Campaign bulk email service.

Provides audience resolution, bulk dispatch, and campaign email analytics.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
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
    CampaignCommunicationPreference,
    CampaignEmailBatch,
    CampaignEmailDelivery,
    CampaignEmailOptOut,
    Donation,
    Donor,
    ExternalCommunicationAuthorization,
    db,
)
from ngo_homesuite.utils.email import send_email
from ngo_homesuite.utils.email import email_connectivity_smoke


logger = logging.getLogger(__name__)
_EMAIL_ADDRESS_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VALID_DIGEST_FREQUENCIES = {"immediate", "daily", "weekly", "monthly"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalized_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _looks_like_email(value: Any) -> bool:
    email = _normalized_email(value)
    if not email:
        return False
    return bool(_EMAIL_ADDRESS_PATTERN.match(email))


def _sender_email() -> str:
    if has_app_context():
        configured = str(current_app.config.get("DEFAULT_MAIL_SENDER") or "").strip()
        if configured:
            return configured.lower()
    return "noreply@ngohomesuite.local"


def _parse_domain_allowlist(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        values = [part.strip().lower() for part in raw.split(",")]
        return {value for value in values if value}
    if isinstance(raw, (list, tuple, set)):
        values = [str(part).strip().lower() for part in raw]
        return {value for value in values if value}
    return set()


def _sender_domain_policy(sender_email: str) -> tuple[bool, set[str], str]:
    allowed_domains = _parse_domain_allowlist(
        current_app.config.get("CAMPAIGN_EMAIL_ALLOWED_SENDER_DOMAINS", "")
    ) if has_app_context() else set()
    sender_domain = _recipient_domain(sender_email)
    if not allowed_domains:
        return True, allowed_domains, sender_domain
    return sender_domain in allowed_domains, allowed_domains, sender_domain


def _public_tracking_url_ready() -> tuple[bool, str]:
    base_url = _tracking_base_url()
    lower = base_url.lower()
    if lower.startswith("https://"):
        return True, base_url

    # Keep local and test environments functional while still surfacing warnings.
    if has_app_context() and bool(current_app.config.get("TESTING", False)):
        return True, base_url

    allow_http_local = bool(current_app.config.get("CAMPAIGN_EMAIL_ALLOW_HTTP_LOCAL_PUBLIC_URL", True)) if has_app_context() else True
    if allow_http_local and ("localhost" in lower or "127.0.0.1" in lower):
        return True, base_url

    return False, base_url


def _deliverability_preflight(
    *,
    organization_id: int,
    campaign_id: int,
    subject: str,
    body: str,
    audience: dict[str, Any] | None,
    recipients: list[Donor] | None = None,
) -> dict[str, Any]:
    enforce = bool(current_app.config.get("CAMPAIGN_EMAIL_ENFORCE_PRECHECKS", True)) if has_app_context() else True
    recipient_rows = recipients if recipients is not None else _resolve_recipients(organization_id, campaign_id, audience)
    total_recipients = int(len(recipient_rows))

    sender = _sender_email()
    domain_ok, allowed_domains, sender_domain = _sender_domain_policy(sender)
    public_url_ok, public_url = _public_tracking_url_ready()

    connectivity = email_connectivity_smoke(probe=False)
    connectivity_ready = bool(connectivity.get("ready", False))

    checks: list[dict[str, Any]] = []
    checks.append({
        "check": "sender_domain_policy",
        "status": "pass" if domain_ok else "fail",
        "message": (
            "Sender domain satisfies configured allowlist."
            if domain_ok else
            "Sender domain is not in CAMPAIGN_EMAIL_ALLOWED_SENDER_DOMAINS."
        ),
    })
    checks.append({
        "check": "provider_connectivity_ready",
        "status": "pass" if connectivity_ready else "fail",
        "message": (
            "At least one email provider is configured."
            if connectivity_ready else
            "No email provider is configured. Configure SendGrid or SMTP before sending."
        ),
    })
    checks.append({
        "check": "tracking_public_url",
        "status": "pass" if public_url_ok else "warn",
        "message": (
            "Tracking links resolve to a valid public URL."
            if public_url_ok else
            "PUBLIC_APP_URL is not HTTPS/public; tracking links may degrade in production."
        ),
    })
    checks.append({
        "check": "recipient_audience",
        "status": "pass" if total_recipients > 0 else "warn",
        "message": (
            f"Audience resolved to {total_recipients} recipients."
            if total_recipients > 0 else
            "Audience resolved to zero recipients."
        ),
    })

    hard_failures = [
        item for item in checks
        if item.get("status") == "fail"
    ]
    blocked = bool(hard_failures) and enforce

    return {
        "enforcement_enabled": bool(enforce),
        "blocked": bool(blocked),
        "block_reasons": [str(item.get("message") or "") for item in hard_failures],
        "checks": checks,
        "sender_email": sender,
        "sender_domain": sender_domain,
        "allowed_sender_domains": sorted(list(allowed_domains)),
        "public_app_url": public_url,
        "provider_readiness": connectivity,
        "total_recipients": total_recipients,
        "quality_hints": _quality_hints(str(subject or ""), str(body or "")),
    }


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


def _preference_signature(*, email: str, organization_id: int, donor_id: int, issued_at: int) -> str:
    payload = "|".join(
        [
            "pref",
            str(email).lower(),
            str(int(organization_id)),
            str(int(donor_id)),
            str(int(issued_at)),
        ]
    )
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


def verify_preference_signature(
    *,
    email: str,
    organization_id: int,
    donor_id: int,
    issued_at: int,
    signature: str,
    max_age_seconds: int = 2592000,
) -> bool:
    now_ts = int(time.time())
    if issued_at <= 0 or issued_at > now_ts:
        return False
    if now_ts - int(issued_at) > int(max(1, max_age_seconds)):
        return False
    expected = _preference_signature(
        email=email,
        organization_id=organization_id,
        donor_id=donor_id,
        issued_at=issued_at,
    )
    return hmac.compare_digest(str(signature or ""), expected)


def preference_center_url(*, email: str, organization_id: int, donor_id: int) -> str:
    base = _tracking_base_url()
    issued_at = int(time.time())
    normalized_email = str(email or "").strip().lower()
    signature = _preference_signature(
        email=normalized_email,
        organization_id=int(organization_id),
        donor_id=int(donor_id),
        issued_at=issued_at,
    )
    return (
        f"{base}/api/v2/campaigns/email/preferences"
        f"?email={quote_plus(normalized_email)}"
        f"&organization_id={int(organization_id)}"
        f"&donor_id={int(donor_id)}"
        f"&ts={issued_at}&sig={signature}"
    )


def get_campaign_communication_preference(
    organization_id: int,
    *,
    email: str,
    donor_id: int | None = None,
) -> dict[str, Any]:
    normalized_email = _normalized_email(email)
    if not _looks_like_email(normalized_email):
        raise ValueError("valid email is required")

    preference = db.session.scalars(
        select(CampaignCommunicationPreference).where(
            CampaignCommunicationPreference.organization_id == int(organization_id),
            CampaignCommunicationPreference.email == normalized_email,
        ).limit(1)
    ).first()

    if preference is None:
        return {
            "organization_id": int(organization_id),
            "donor_id": int(donor_id) if donor_id else None,
            "email": normalized_email,
            "newsletter_opt_in": True,
            "campaign_opt_in": True,
            "events_opt_in": True,
            "volunteer_opt_in": True,
            "digest_frequency": "weekly",
            "source": "default",
            "updated_at": None,
        }

    return {
        "organization_id": int(preference.organization_id),
        "donor_id": int(preference.donor_id) if preference.donor_id else None,
        "email": str(preference.email or "").strip().lower(),
        "newsletter_opt_in": bool(preference.newsletter_opt_in),
        "campaign_opt_in": bool(preference.campaign_opt_in),
        "events_opt_in": bool(preference.events_opt_in),
        "volunteer_opt_in": bool(preference.volunteer_opt_in),
        "digest_frequency": str(preference.digest_frequency or "weekly"),
        "source": str(preference.source or "preference_center"),
        "updated_at": preference.updated_at.isoformat() if preference.updated_at else None,
    }


def upsert_campaign_communication_preference(
    organization_id: int,
    *,
    email: str,
    donor_id: int | None = None,
    newsletter_opt_in: bool | None = None,
    campaign_opt_in: bool | None = None,
    events_opt_in: bool | None = None,
    volunteer_opt_in: bool | None = None,
    digest_frequency: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    normalized_email = _normalized_email(email)
    if not _looks_like_email(normalized_email):
        raise ValueError("valid email is required")

    preference = db.session.scalars(
        select(CampaignCommunicationPreference).where(
            CampaignCommunicationPreference.organization_id == int(organization_id),
            CampaignCommunicationPreference.email == normalized_email,
        ).limit(1)
    ).first()

    if preference is None:
        preference = CampaignCommunicationPreference(
            organization_id=int(organization_id),
            donor_id=int(donor_id) if donor_id else None,
            email=normalized_email,
            newsletter_opt_in=True,
            campaign_opt_in=True,
            events_opt_in=True,
            volunteer_opt_in=True,
            digest_frequency="weekly",
            source=str(source or "preference_center")[:40],
        )
        db.session.add(preference)
    elif donor_id and not preference.donor_id:
        preference.donor_id = int(donor_id)

    if newsletter_opt_in is not None:
        preference.newsletter_opt_in = bool(newsletter_opt_in)
    if campaign_opt_in is not None:
        preference.campaign_opt_in = bool(campaign_opt_in)
    if events_opt_in is not None:
        preference.events_opt_in = bool(events_opt_in)
    if volunteer_opt_in is not None:
        preference.volunteer_opt_in = bool(volunteer_opt_in)

    if digest_frequency is not None:
        normalized_frequency = str(digest_frequency or "").strip().lower()
        if normalized_frequency not in _VALID_DIGEST_FREQUENCIES:
            raise ValueError("digest_frequency must be one of immediate, daily, weekly, monthly")
        preference.digest_frequency = normalized_frequency

    if source is not None:
        preference.source = str(source or "")[:40] or None

    db.session.commit()
    return get_campaign_communication_preference(
        int(organization_id),
        email=normalized_email,
        donor_id=donor_id,
    )


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
    organization_id: int,
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
        pref_url = preference_center_url(
            email=str(recipient_email).lower(),
            organization_id=int(organization_id),
            donor_id=int(donor_id),
        )
        footer = (
            "\n\n<div style=\"margin-top:2em;padding-top:1em;border-top:1px solid #e0e0e0;"
            "font-size:12px;color:#999;text-align:center;font-family:Arial,sans-serif;\">"
            "<p style=\"margin:0 0 4px;\">You are receiving this email as a valued supporter.</p>"
            f"<p style=\"margin:0;\"><a href=\"{unsub_url}\" "
            "style=\"color:#999;text-decoration:underline;\">Unsubscribe</a> "
            "from campaign emails. "
            f"<a href=\"{pref_url}\" style=\"color:#999;text-decoration:underline;\">Manage preferences</a>."
            "</p></div>"
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

    smart_group_rules_raw = audience.get("smart_group_rules")
    smart_group_rules_member_ids: list[int] | None = None
    if smart_group_rules_raw is not None:
        if not isinstance(smart_group_rules_raw, list):
            raise ValueError("smart_group_rules must be a list of rule objects")

        from ngo_homesuite.services.smart_groups_service import evaluate_rules

        try:
            members = evaluate_rules(int(organization_id), smart_group_rules_raw)
        except ValueError as exc:
            raise ValueError(f"invalid smart_group_rules: {exc}") from exc

        smart_group_rules_member_ids = [
            int(item.get("donor_id"))
            for item in members
            if str(item.get("donor_id") or "").strip().isdigit()
        ]
        if not smart_group_rules_member_ids:
            return []

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

    include_communication_opt_out = bool(audience.get("include_communication_opt_out", False))
    channel = str(audience.get("channel") or "campaign").strip().lower() or "campaign"

    stmt = select(Donor).where(
        Donor.organization_id == int(organization_id),
        Donor.email.is_not(None),
        func.length(func.trim(Donor.email)) > 0,
    )
    if not include_communication_opt_out:
        stmt = stmt.where(Donor.communication_opt_in.is_(True))

    if smart_group_member_ids is not None:
        stmt = stmt.where(Donor.id.in_(smart_group_member_ids))
    if smart_group_rules_member_ids is not None:
        stmt = stmt.where(Donor.id.in_(smart_group_rules_member_ids))

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
    max_total_given_raw = audience.get("max_total_given")
    max_total_given = float(max_total_given_raw) if max_total_given_raw is not None else None
    if max_total_given is not None and max_total_given < min_total_given:
        raise ValueError("max_total_given must be greater than or equal to min_total_given")

    campaign_donors_only = bool(audience.get("campaign_donors_only", False))
    gifted_within_days = int(audience.get("gifted_within_days") or 0)
    lapsed_days_min = int(audience.get("lapsed_days_min") or 0)

    min_gift_count_raw = audience.get("min_gift_count")
    max_gift_count_raw = audience.get("max_gift_count")
    min_gift_count = int(min_gift_count_raw) if min_gift_count_raw is not None else 0
    max_gift_count = int(max_gift_count_raw) if max_gift_count_raw is not None else None
    if min_gift_count < 0:
        raise ValueError("min_gift_count must be greater than or equal to 0")
    if max_gift_count is not None and max_gift_count < 0:
        raise ValueError("max_gift_count must be greater than or equal to 0")
    if max_gift_count is not None and max_gift_count < min_gift_count:
        raise ValueError("max_gift_count must be greater than or equal to min_gift_count")

    gifted_between_days_min_raw = audience.get("gifted_between_days_min")
    gifted_between_days_max_raw = audience.get("gifted_between_days_max")
    gifted_between_days_min = int(gifted_between_days_min_raw) if gifted_between_days_min_raw is not None else None
    gifted_between_days_max = int(gifted_between_days_max_raw) if gifted_between_days_max_raw is not None else None
    if gifted_between_days_min is not None and gifted_between_days_min < 0:
        raise ValueError("gifted_between_days_min must be greater than or equal to 0")
    if gifted_between_days_max is not None and gifted_between_days_max < 0:
        raise ValueError("gifted_between_days_max must be greater than or equal to 0")
    if (
        gifted_between_days_min is not None
        and gifted_between_days_max is not None
        and gifted_between_days_max < gifted_between_days_min
    ):
        raise ValueError("gifted_between_days_max must be greater than or equal to gifted_between_days_min")

    recipients: list[Donor] = []
    for donor in base_recipients:
        donor_metrics = metrics.get(int(donor.id), {"total_given": 0.0, "gift_count": 0, "last_gift_at": None})
        total_given = float(donor_metrics.get("total_given", 0.0))
        gift_count = int(donor_metrics.get("gift_count", 0) or 0)

        if total_given < min_total_given:
            continue
        if max_total_given is not None and total_given > max_total_given:
            continue
        if gift_count < min_gift_count:
            continue
        if max_gift_count is not None and gift_count > max_gift_count:
            continue
        if campaign_donors_only and float(campaign_totals.get(int(donor.id), 0.0)) <= 0.0:
            continue

        last_gift_at = donor_metrics.get("last_gift_at")
        days_since_last_gift = (now - last_gift_at).days if last_gift_at else None
        if gifted_within_days > 0:
            if not last_gift_at:
                continue
            if days_since_last_gift is not None and days_since_last_gift > gifted_within_days:
                continue

        if gifted_between_days_min is not None or gifted_between_days_max is not None:
            if days_since_last_gift is None:
                continue
            if gifted_between_days_min is not None and days_since_last_gift < gifted_between_days_min:
                continue
            if gifted_between_days_max is not None and days_since_last_gift > gifted_between_days_max:
                continue

        if lapsed_days_min > 0:
            if not last_gift_at:
                # No giving history counts as lapsed for this audience mode.
                pass
            elif days_since_last_gift is not None and days_since_last_gift < lapsed_days_min:
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
        email_key = _normalized_email(donor.email)
        if not _looks_like_email(email_key):
            continue
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

    # Respect preference-center channel opt-out choices.
    try:
        pref_rows = db.session.execute(
            select(
                CampaignCommunicationPreference.email,
                CampaignCommunicationPreference.newsletter_opt_in,
                CampaignCommunicationPreference.campaign_opt_in,
                CampaignCommunicationPreference.events_opt_in,
                CampaignCommunicationPreference.volunteer_opt_in,
            ).where(CampaignCommunicationPreference.organization_id == int(organization_id))
        ).all()

        blocked: set[str] = set()
        for email, newsletter_opt_in, campaign_opt_in, events_opt_in, volunteer_opt_in in pref_rows:
            normalized_email = _normalized_email(email)
            if not normalized_email:
                continue
            if channel == "newsletter" and not bool(newsletter_opt_in):
                blocked.add(normalized_email)
            elif channel == "events" and not bool(events_opt_in):
                blocked.add(normalized_email)
            elif channel == "volunteer" and not bool(volunteer_opt_in):
                blocked.add(normalized_email)
            elif channel in {"campaign", "fundraising"} and not bool(campaign_opt_in):
                blocked.add(normalized_email)

        if blocked:
            deduped = {k: v for k, v in deduped.items() if k not in blocked}
    except Exception as exc:
        logger.warning(
            "Unable to apply communication preference suppression for organization_id=%s: %s",
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
    audience = audience or {}
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

    donor_id_list = [int(d.id) for d in recipients]
    metrics = _donor_giving_metrics(int(organization_id), donor_id_list)
    now = _utcnow()

    total_giving_bands = {
        "0-99": 0,
        "100-499": 0,
        "500-999": 0,
        "1000+": 0,
    }
    gift_count_bands = {
        "0": 0,
        "1": 0,
        "2-5": 0,
        "6+": 0,
    }
    recency_bands = {
        "0-30d": 0,
        "31-90d": 0,
        "91-365d": 0,
        "365d+": 0,
        "never": 0,
    }

    for donor in recipients:
        donor_metrics = metrics.get(int(donor.id), {"total_given": 0.0, "gift_count": 0, "last_gift_at": None})
        total_given = float(donor_metrics.get("total_given", 0.0) or 0.0)
        gift_count = int(donor_metrics.get("gift_count", 0) or 0)
        last_gift_at = donor_metrics.get("last_gift_at")

        if total_given >= 1000.0:
            total_giving_bands["1000+"] += 1
        elif total_given >= 500.0:
            total_giving_bands["500-999"] += 1
        elif total_given >= 100.0:
            total_giving_bands["100-499"] += 1
        else:
            total_giving_bands["0-99"] += 1

        if gift_count >= 6:
            gift_count_bands["6+"] += 1
        elif gift_count >= 2:
            gift_count_bands["2-5"] += 1
        elif gift_count == 1:
            gift_count_bands["1"] += 1
        else:
            gift_count_bands["0"] += 1

        if not last_gift_at:
            recency_bands["never"] += 1
        else:
            days_since = (now - last_gift_at).days
            if days_since <= 30:
                recency_bands["0-30d"] += 1
            elif days_since <= 90:
                recency_bands["31-90d"] += 1
            elif days_since <= 365:
                recency_bands["91-365d"] += 1
            else:
                recency_bands["365d+"] += 1

    return {
        "campaign_id": int(campaign.id),
        "campaign_name": campaign.name,
        "total_recipients": len(recipients),
        "segment_breakdown": by_type,
        "recipient_breakdown": {
            "by_donor_type": by_type,
            "by_total_giving_band": total_giving_bands,
            "by_gift_count_band": gift_count_bands,
            "by_recency_band": recency_bands,
        },
        "audience_applied": dict(audience),
        "quality_hints": _quality_hints(str(subject), str(body)),
        "sample_preview": previews,
    }


def campaign_email_deliverability_report(
    organization_id: int,
    campaign_id: int,
    *,
    subject: str,
    body: str,
    audience: dict[str, Any] | None = None,
) -> dict[str, Any]:
    campaign = _get_campaign_or_raise(campaign_id, organization_id)
    report = _deliverability_preflight(
        organization_id=int(organization_id),
        campaign_id=int(campaign.id),
        subject=str(subject or ""),
        body=str(body or ""),
        audience=audience,
    )
    report.update({
        "campaign_id": int(campaign.id),
        "campaign_name": campaign.name,
    })
    return report


def campaign_email_automation_templates(campaign: Campaign) -> list[dict[str, Any]]:
    campaign_name = str(campaign.name or "Our Campaign").strip() or "Our Campaign"
    return [
        {
            "key": "welcome_nurture",
            "label": "Welcome Nurture (3-touch)",
            "step_count": 3,
            "cadence_days": 7,
            "subject": f"Welcome to {campaign_name}",
            "body": (
                "Hi {name},\n\n"
                f"Thank you for supporting {campaign_name}. Over the next few weeks, "
                "we will share updates showing the impact your support makes.\n\n"
                "With gratitude,\n"
                "Fundraising Team"
            ),
        },
        {
            "key": "lapsed_reengagement",
            "label": "Lapsed Donor Re-engagement",
            "step_count": 4,
            "cadence_days": 10,
            "subject": f"{campaign_name}: we'd love to reconnect",
            "body": (
                "Hi {name},\n\n"
                f"You have been an important part of {campaign_name}. "
                "If now is a good time, we would love to welcome you back and share what has changed since your last gift.\n\n"
                "Thank you for your continued care,\n"
                "Fundraising Team"
            ),
        },
        {
            "key": "deadline_last_call",
            "label": "Deadline Last Call",
            "step_count": 2,
            "cadence_days": 3,
            "subject": f"Last chance to support {campaign_name}",
            "body": (
                "Hi {name},\n\n"
                f"We are in the final stretch for {campaign_name}. "
                "A gift today helps us finish strong before this deadline.\n\n"
                "Thank you for standing with us,\n"
                "Fundraising Team"
            ),
        },
    ]


def _template_by_key(campaign: Campaign, template_key: str) -> dict[str, Any]:
    key = str(template_key or "").strip().lower()
    for template in campaign_email_automation_templates(campaign):
        if str(template.get("key") or "").strip().lower() == key:
            return template
    raise ValueError("unknown automation template key")


@enforce_error_contract
def instantiate_campaign_email_automation_template(
    organization_id: int,
    campaign_id: int,
    *,
    template_key: str,
    created_by_user_id: int | None,
    created_by_username: str | None,
    created_by_role: str | None,
    audience: dict[str, Any] | None,
    human_authorization: dict[str, Any] | None,
    start_at: datetime | None = None,
) -> dict[str, Any]:
    campaign = _get_campaign_or_raise(campaign_id, organization_id)
    template = _template_by_key(campaign, template_key)
    result = schedule_campaign_email_sequence(
        organization_id=int(organization_id),
        campaign_id=int(campaign.id),
        created_by_user_id=created_by_user_id,
        created_by_username=created_by_username,
        created_by_role=created_by_role,
        subject=str(template.get("subject") or ""),
        body=str(template.get("body") or ""),
        audience=audience,
        human_authorization=human_authorization,
        step_count=int(template.get("step_count") or 1),
        cadence_days=int(template.get("cadence_days") or 7),
        start_at=start_at,
    )
    result["template_key"] = str(template.get("key") or "")
    result["template_label"] = str(template.get("label") or "")
    return result


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

    preflight = _deliverability_preflight(
        organization_id=int(organization_id),
        campaign_id=int(campaign.id),
        subject=subject_value,
        body=body_value,
        audience=audience,
        recipients=recipients,
    )
    if bool(preflight.get("blocked", False)):
        reason = "; ".join([str(item) for item in preflight.get("block_reasons") or []]).strip()
        raise ValueError(f"deliverability precheck failed: {reason or 'configuration is not ready'}")

    if bool(dry_run):
        return {
            "dry_run": True,
            "campaign_id": int(campaign.id),
            "total_recipients": len(recipients),
            "sample_emails": [str(r.email) for r in recipients[:20]],
            "deliverability": preflight,
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
                    organization_id=int(organization_id),
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
        "deliverability": preflight,
    }


@enforce_error_contract
def schedule_campaign_email_sequence(
    organization_id: int,
    campaign_id: int,
    *,
    created_by_user_id: int | None,
    created_by_username: str | None,
    created_by_role: str | None,
    subject: str,
    body: str,
    audience: dict[str, Any] | None,
    human_authorization: dict[str, Any] | None,
    step_count: int = 3,
    cadence_days: int = 7,
    start_at: datetime | None = None,
) -> dict[str, Any]:
    steps = max(1, min(int(step_count or 1), 12))
    cadence = max(1, min(int(cadence_days or 1), 90))
    first_send_at = start_at if isinstance(start_at, datetime) else (_utcnow() + timedelta(minutes=5))

    sequence_results: list[dict[str, Any]] = []
    for index in range(steps):
        scheduled_at = first_send_at + timedelta(days=(index * cadence))
        sequence_subject = str(subject or "").strip()
        if steps > 1:
            sequence_subject = f"{sequence_subject} [{index + 1}/{steps}]"

        result = send_campaign_bulk_email(
            organization_id=int(organization_id),
            campaign_id=int(campaign_id),
            created_by_user_id=created_by_user_id,
            created_by_username=created_by_username,
            created_by_role=created_by_role,
            subject=sequence_subject,
            body=body,
            audience=audience,
            human_authorization=human_authorization,
            dry_run=False,
            scheduled_at=scheduled_at,
        )
        sequence_results.append(
            {
                "step": int(index + 1),
                "batch_id": int(result.get("batch_id") or 0),
                "scheduled_at": str(result.get("scheduled_at") or scheduled_at.isoformat()),
                "total_recipients": int(result.get("total_recipients") or 0),
                "status": str(result.get("status") or "scheduled"),
            }
        )

    return {
        "campaign_id": int(campaign_id),
        "automation": "drip_sequence",
        "step_count": int(steps),
        "cadence_days": int(cadence),
        "batches": sequence_results,
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
                            organization_id=int(batch.organization_id),
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


def campaign_email_attribution(
    organization_id: int,
    campaign_id: int,
    *,
    window_days: int = 30,
) -> dict[str, Any]:
    campaign = _get_campaign_or_raise(campaign_id, organization_id)
    window = max(1, min(int(window_days or 30), 365))
    completed_statuses = ("received", "processed", "receipted")

    deliveries = db.session.execute(
        select(
            CampaignEmailDelivery.id,
            CampaignEmailDelivery.donor_id,
            CampaignEmailDelivery.sent_at,
        ).where(
            CampaignEmailDelivery.organization_id == int(organization_id),
            CampaignEmailDelivery.campaign_id == int(campaign.id),
            CampaignEmailDelivery.delivery_status == "sent",
            CampaignEmailDelivery.sent_at.is_not(None),
            CampaignEmailDelivery.donor_id.is_not(None),
        )
    ).all()

    last_touch_by_donor: dict[int, datetime] = {}
    sent_delivery_count = 0
    for _delivery_id, donor_id, sent_at in deliveries:
        if donor_id is None or sent_at is None:
            continue
        sent_delivery_count += 1
        donor_key = int(donor_id)
        previous = last_touch_by_donor.get(donor_key)
        if previous is None or sent_at > previous:
            last_touch_by_donor[donor_key] = sent_at

    if not last_touch_by_donor:
        return {
            "campaign_id": int(campaign.id),
            "campaign_name": campaign.name,
            "window_days": int(window),
            "sent_deliveries": int(sent_delivery_count),
            "influenced_donations": 0,
            "influenced_revenue": 0.0,
            "influenced_donor_count": 0,
            "average_gift": 0.0,
            "influenced_revenue_7d": 0.0,
            "influenced_revenue_30d": 0.0,
            "influenced_revenue_90d": 0.0,
            "top_influenced_donors": [],
        }

    donor_ids = sorted(last_touch_by_donor.keys())
    donation_rows = db.session.execute(
        select(
            Donation.id,
            Donation.donor_id,
            Donation.amount,
            Donation.donation_date,
        ).where(
            Donation.organization_id == int(organization_id),
            Donation.status.in_(completed_statuses),
            Donation.donor_id.in_(donor_ids),
            Donation.donation_date.is_not(None),
        )
    ).all()

    influenced_donations = 0
    influenced_revenue = 0.0
    influenced_revenue_7d = 0.0
    influenced_revenue_30d = 0.0
    influenced_revenue_90d = 0.0
    donor_totals: dict[int, float] = defaultdict(float)

    for _donation_id, donor_id, amount, donation_date in donation_rows:
        if donor_id is None or donation_date is None:
            continue
        last_touch = last_touch_by_donor.get(int(donor_id))
        if last_touch is None:
            continue
        delta = donation_date - last_touch
        if delta.total_seconds() < 0:
            continue
        delta_days = delta.days
        if delta_days > window:
            continue

        gift_amount = float(amount or 0.0)
        influenced_donations += 1
        influenced_revenue += gift_amount
        donor_totals[int(donor_id)] += gift_amount
        if delta_days <= 7:
            influenced_revenue_7d += gift_amount
        if delta_days <= 30:
            influenced_revenue_30d += gift_amount
        if delta_days <= 90:
            influenced_revenue_90d += gift_amount

    donor_name_rows = db.session.execute(
        select(Donor.id, Donor.name).where(
            Donor.organization_id == int(organization_id),
            Donor.id.in_(list(donor_totals.keys()) if donor_totals else [-1]),
        )
    ).all()
    donor_names = {int(did): str(name or "Donor") for did, name in donor_name_rows}

    top_donors = sorted(donor_totals.items(), key=lambda item: item[1], reverse=True)[:10]

    return {
        "campaign_id": int(campaign.id),
        "campaign_name": campaign.name,
        "window_days": int(window),
        "sent_deliveries": int(sent_delivery_count),
        "influenced_donations": int(influenced_donations),
        "influenced_revenue": round(float(influenced_revenue), 2),
        "influenced_donor_count": int(len(donor_totals)),
        "average_gift": round(float(influenced_revenue / influenced_donations), 2) if influenced_donations > 0 else 0.0,
        "influenced_revenue_7d": round(float(influenced_revenue_7d), 2),
        "influenced_revenue_30d": round(float(influenced_revenue_30d), 2),
        "influenced_revenue_90d": round(float(influenced_revenue_90d), 2),
        "top_influenced_donors": [
            {
                "donor_id": int(donor_id),
                "donor_name": donor_names.get(int(donor_id), "Donor"),
                "revenue": round(float(total), 2),
            }
            for donor_id, total in top_donors
        ],
    }
