from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

import requests
from flask import current_app, has_app_context, render_template

logger = logging.getLogger(__name__)


def _setting(name: str, default: str | None = None) -> str | None:
    if has_app_context() and name in current_app.config:
        value = current_app.config.get(name)
        return default if value is None else str(value)
    return os.getenv(name, default)


def _render_message(template: str | None, context: dict[str, Any] | None) -> str:
    context = context or {}
    if template and has_app_context():
        try:
            return render_template(template, **context)
        except Exception:
            pass
    return str(context.get("text") or "Notification from NGO HomeSuite")


def email_connectivity_smoke(*, probe: bool = False) -> dict[str, Any]:
    """Return non-destructive readiness/probe results for configured email providers.

    When ``probe`` is false, this reports configuration readiness only.
    When ``probe`` is true, it additionally performs provider connectivity checks
    without sending an actual message.
    """
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    smtp_host = _setting("MAIL_SERVER")
    smtp_port = int(_setting("MAIL_PORT", "25") or "25")
    smtp_user = _setting("MAIL_USERNAME")
    smtp_password = _setting("MAIL_PASSWORD")
    use_tls = (_setting("MAIL_USE_TLS", "false") or "false").lower() in {"1", "true", "yes"}
    default_sender = _setting("DEFAULT_MAIL_SENDER", "noreply@ngohomesuite.local")

    sendgrid_configured = bool(sendgrid_key)
    smtp_configured = bool(smtp_host)

    result: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "probe": bool(probe),
        "default_mail_sender": default_sender,
        "providers": {
            "sendgrid": {
                "configured": sendgrid_configured,
                "probed": False,
                "ok": None,
                "error": None,
            },
            "smtp": {
                "configured": smtp_configured,
                "host": smtp_host,
                "port": smtp_port,
                "use_tls": use_tls,
                "probed": False,
                "ok": None,
                "error": None,
            },
        },
        "ready": bool(sendgrid_configured or smtp_configured),
    }

    if not probe:
        return result

    if sendgrid_configured:
        provider = result["providers"]["sendgrid"]
        provider["probed"] = True
        try:
            resp = requests.get(
                "https://api.sendgrid.com/v3/user/account",
                headers={"Authorization": f"Bearer {sendgrid_key}"},
                timeout=10,
            )
            if 200 <= resp.status_code < 300:
                provider["ok"] = True
            else:
                provider["ok"] = False
                provider["error"] = f"sendgrid_http_{resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            provider["ok"] = False
            provider["error"] = str(exc)

    if smtp_configured:
        provider = result["providers"]["smtp"]
        provider["probed"] = True
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.ehlo()
                if use_tls:
                    server.starttls()
                    server.ehlo()
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
            provider["ok"] = True
        except Exception as exc:  # noqa: BLE001
            provider["ok"] = False
            provider["error"] = str(exc)

    sendgrid_ok = result["providers"]["sendgrid"]["ok"] is True
    smtp_ok = result["providers"]["smtp"]["ok"] is True
    result["ready"] = bool(sendgrid_ok or smtp_ok)
    return result


def send_email(*, to: str, subject: str, template: str | None = None, context: dict[str, Any] | None = None) -> bool:
    """Send email via SendGrid when configured, otherwise SMTP fallback."""
    to_email = (to or "").strip()
    if not to_email:
        return False

    body = _render_message(template, context)

    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    from_email = _setting("DEFAULT_MAIL_SENDER", "noreply@ngohomesuite.local")
    if sendgrid_key:
        try:
            resp = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {sendgrid_key}",
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
                return True
            logger.warning("SendGrid delivery failed with status %s", resp.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SendGrid delivery error: %s", exc)

    smtp_host = _setting("MAIL_SERVER")
    smtp_port = int(_setting("MAIL_PORT", "25") or "25")
    smtp_user = _setting("MAIL_USERNAME")
    smtp_password = _setting("MAIL_PASSWORD")
    use_tls = (_setting("MAIL_USE_TLS", "false") or "false").lower() in {"1", "true", "yes"}
    if not smtp_host:
        return False

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
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("SMTP delivery failed: %s", exc)
        return False
