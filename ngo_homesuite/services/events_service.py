"""Events reminder delivery service.

Provides a concrete reminder sender for event attendees with SendGrid-first
and SMTP fallback delivery.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any

import requests
from flask import current_app, has_app_context
from sqlalchemy import text

from ngo_homesuite.models.core import db

logger = logging.getLogger(__name__)


def _mail_setting(name: str, default: str | None = None) -> str | None:
    if has_app_context() and name in current_app.config:
        value = current_app.config.get(name)
        if value is None:
            return default
        return str(value)
    return os.getenv(name, default)


def _load_event(event_id: int) -> dict[str, Any] | None:
    """Load event details from legacy events table if available."""
    stmt = text(
        """
        SELECT id, title, start_datetime, location
        FROM events
        WHERE id = :event_id
        """
    )
    row = db.session.execute(stmt, {"event_id": int(event_id)}).mappings().first()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "title": str(row["title"] or "Community Event"),
        "start_datetime": row["start_datetime"],
        "location": row["location"],
    }


def _send_via_sendgrid(*, to_email: str, subject: str, body: str) -> tuple[bool, str | None]:
    api_key = os.getenv("SENDGRID_API_KEY")
    if not api_key:
        return False, "missing_sendgrid_api_key"

    from_email = _mail_setting("DEFAULT_MAIL_SENDER", "noreply@ngohomesuite.local")
    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        },
        timeout=10,
    )
    if 200 <= resp.status_code < 300:
        return True, None
    return False, f"sendgrid_http_{resp.status_code}"


def _send_via_smtp(*, to_email: str, subject: str, body: str) -> tuple[bool, str | None]:
    smtp_host = _mail_setting("MAIL_SERVER")
    smtp_port = int(_mail_setting("MAIL_PORT", "25") or "25")
    smtp_user = _mail_setting("MAIL_USERNAME")
    smtp_password = _mail_setting("MAIL_PASSWORD")
    use_tls = (_mail_setting("MAIL_USE_TLS", "false") or "false").lower() in {"1", "true", "yes"}
    from_email = _mail_setting("DEFAULT_MAIL_SENDER", "noreply@ngohomesuite.local")

    if not smtp_host or not to_email:
        return False, "missing_smtp_settings"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if use_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("SMTP send failed for %s: %s", to_email, exc)
        return False, str(exc)


def send_reminder(event_id: int, attendee_email: str) -> dict[str, Any]:
    """Send a reminder email for an event attendee.

    Delivery policy:
    1) Try SendGrid if configured.
    2) Fallback to SMTP using MAIL_* settings.
    """
    email = (attendee_email or "").strip()
    if not email:
        return {"sent": False, "provider": "none", "error": "missing_attendee_email"}

    event = _load_event(event_id)
    if event is None:
        return {"sent": False, "provider": "none", "error": "event_not_found"}

    subject = f"Reminder: {event['title']}"
    when = event.get("start_datetime") or "TBD"
    location = event.get("location") or "TBD"
    body = (
        f"This is a reminder for your upcoming event.\n\n"
        f"Event: {event['title']}\n"
        f"When: {when}\n"
        f"Location: {location}\n\n"
        f"Thank you for supporting our community programs."
    )

    ok, err = _send_via_sendgrid(to_email=email, subject=subject, body=body)
    if ok:
        return {"sent": True, "provider": "sendgrid", "error": None}

    ok, smtp_err = _send_via_smtp(to_email=email, subject=subject, body=body)
    if ok:
        return {"sent": True, "provider": "smtp", "error": None}

    return {
        "sent": False,
        "provider": "none",
        "error": smtp_err or err or "delivery_failed",
    }
