"""SMS messaging service with Twilio integration.

Environment variables required when Twilio is configured:
    TWILIO_ACCOUNT_SID  — Twilio account SID
    TWILIO_AUTH_TOKEN   — Twilio auth token
    TWILIO_FROM_NUMBER  — E.164 sender number, e.g. +15551234567

When variables are absent the service logs and no-ops (offline/stub mode).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_STUB_MODE = False  # overrideable in tests


def _is_configured() -> bool:
    return all(
        os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    )


def send_sms(to: str, body: str, *, from_number: Optional[str] = None) -> bool:
    """Send an SMS message.

    Returns True on success, False on failure or stub mode.
    ``to`` must be an E.164 phone number (e.g. +15551234567).
    """
    if _STUB_MODE:
        logger.info("[SMS STUB] to=%s body=%s", to, body[:80])
        return True

    if not _is_configured():
        logger.warning(
            "SMS not configured — TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER missing. "
            "Message to %s dropped.",
            to,
        )
        return False

    try:
        from twilio.rest import Client  # type: ignore[import]

        client = Client(
            os.environ["TWILIO_ACCOUNT_SID"],
            os.environ["TWILIO_AUTH_TOKEN"],
        )
        message = client.messages.create(
            body=body,
            from_=from_number or os.environ["TWILIO_FROM_NUMBER"],
            to=to,
        )
        logger.info("SMS sent sid=%s to=%s", message.sid, to)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("SMS send failed to=%s: %s", to, exc)
        return False


def send_bulk_sms(recipients: list[dict], body_template: str) -> dict:
    """Send SMS to a list of recipients with simple {name} substitution.

    recipients: list of {"phone": "+1...", "name": "Alice", ...}
    Returns {"sent": N, "failed": N}
    """
    sent = 0
    failed = 0
    for r in recipients:
        phone = r.get("phone") or ""
        if not phone:
            failed += 1
            continue
        body = body_template.format(**r)
        ok = send_sms(phone, body)
        if ok:
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed}


def notify_appointment_reminder(
    phone: str,
    beneficiary_name: str,
    appointment_title: str,
    scheduled_at_str: str,
) -> bool:
    """Send a formatted appointment reminder SMS."""
    body = (
        f"Reminder: {beneficiary_name}, your appointment '{appointment_title}' "
        f"is scheduled for {scheduled_at_str}. "
        "If you need to reschedule please contact your case worker."
    )
    return send_sms(phone, body)


def notify_referral_update(
    phone: str,
    beneficiary_name: str,
    provider_name: str,
    status: str,
) -> bool:
    """Send a referral status update SMS."""
    body = (
        f"Hi {beneficiary_name}, your referral to {provider_name} "
        f"has been updated to: {status}. "
        "Contact your case worker for details."
    )
    return send_sms(phone, body)

