from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, render_template_string
from flask_login import current_user, login_required
from sqlalchemy import text

from ngo_homesuite.services.calendar_sync_service import InMemoryCalendarProvider, sync_task_deadlines
from ngo_homesuite.services.calendar_sync_service import (
    InMemoryDavSyncProvider,
    sync_donor_contacts_to_carddav,
    sync_task_deadlines_to_caldav,
)
from ngo_homesuite.services.integration_ops_service import (
    get_background_job,
    list_background_jobs,
    record_integration_event,
    run_with_backoff,
    submit_background_job,
    summarize_integration_events,
)
from ngo_homesuite.utils.payment_webhooks import ReplayGuard, event_id, verify_stripe_signature
from ngo_homesuite.web.rbac import roles_required
from ngo_homesuite.models.core import db
from ngo_homesuite.events.services import process_email_opt_out


integrations_bp = Blueprint("integrations", __name__, url_prefix="/integrations")



def _stripe_replay_guard() -> ReplayGuard:
    guard = current_app.extensions.get("stripe_webhook_replay_guard")
    if guard is None:
        guard = ReplayGuard(ttl_seconds=3600)
        current_app.extensions["stripe_webhook_replay_guard"] = guard
    return guard



def _calendar_provider() -> InMemoryCalendarProvider:
    provider = current_app.extensions.get("calendar_sync_provider")
    if provider is None:
        provider = InMemoryCalendarProvider()
        current_app.extensions["calendar_sync_provider"] = provider
    return provider


def _dav_provider() -> InMemoryDavSyncProvider:
    provider = current_app.extensions.get("dav_sync_provider")
    if provider is None:
        provider = InMemoryDavSyncProvider()
        current_app.extensions["dav_sync_provider"] = provider
    return provider


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_registration_payment_columns() -> None:
    cols = db.session.execute(text("PRAGMA table_info(registrations)")).mappings().all()
    names = {str(c.get("name") or "") for c in cols}
    if "payment_status" not in names:
        db.session.execute(text("ALTER TABLE registrations ADD COLUMN payment_status TEXT DEFAULT 'pending'"))
    if "payment_reference" not in names:
        db.session.execute(text("ALTER TABLE registrations ADD COLUMN payment_reference TEXT"))
    db.session.commit()


def _upsert_event_registration_paid(*, event_id: int, donor_id: int, reference: str) -> None:
    _ensure_registration_payment_columns()
    existing = db.session.execute(
        text(
            """
            SELECT id
            FROM registrations
            WHERE event_id = :event_id
              AND donor_id = :donor_id
              AND deleted_at IS NULL
            LIMIT 1
            """
        ),
        {"event_id": int(event_id), "donor_id": int(donor_id)},
    ).mappings().first()

    if existing:
        db.session.execute(
            text(
                """
                UPDATE registrations
                SET payment_status = 'paid',
                    payment_reference = :reference,
                    updated_at = :now_iso
                WHERE id = :id
                """
            ),
            {"id": int(existing["id"]), "reference": str(reference), "now_iso": _utcnow_iso()},
        )
    else:
        db.session.execute(
            text(
                """
                INSERT INTO registrations(event_id, donor_id, registered_at, payment_status, payment_reference, updated_at)
                VALUES (:event_id, :donor_id, :now_iso, 'paid', :reference, :now_iso)
                """
            ),
            {
                "event_id": int(event_id),
                "donor_id": int(donor_id),
                "reference": str(reference),
                "now_iso": _utcnow_iso(),
            },
        )
    db.session.commit()


@integrations_bp.post("/stripe/checkout")
@login_required
@roles_required("admin", "staff")
def create_stripe_checkout_route():
    """Create a Stripe Checkout session for a donation.

    Request body (JSON):
        campaign_name (str, required)
        amount_cents  (int, required)   â€“ amount in the smallest currency unit
        currency      (str, default USD)
        success_url   (str, required)
        cancel_url    (str, required)
        donor_id      (int, optional)
        campaign_id   (int, optional)   â€“ maps to project_id in the Donation model
        donor_email   (str, optional)
    """
    from ngo_homesuite.services.payment_service import PaymentService, StripeNotConfigured

    data = request.get_json(silent=True) or {}
    required = ["campaign_name", "amount_cents", "success_url", "cancel_url"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    org_id = current_user.organization_id
    if not org_id:
        return jsonify({"error": "User has no associated organisation"}), 403

    try:
        amount_cents = int(data["amount_cents"])
    except (ValueError, TypeError):
        return jsonify({"error": "amount_cents must be an integer"}), 400

    try:
        donor_name = data.get("donor_name")
        if not donor_name and current_user:
            donor_name = getattr(current_user, "name", None) or getattr(current_user, "username", None)

        campaign_name = str(data.get("campaign_name") or "Donation")
        if data.get("event_id"):
            campaign_name = f"Event Registration: {campaign_name}"

        result = PaymentService().create_checkout_session(
            org_id=org_id,
            donor_id=data.get("donor_id"),
            campaign_id=data.get("campaign_id"),
            event_id=data.get("event_id"),
            donation_id=data.get("donation_id"),
            amount_cents=amount_cents,
            currency=data.get("currency", "USD"),
            campaign_name=campaign_name,
            success_url=data["success_url"],
            cancel_url=data["cancel_url"],
            donor_email=data.get("donor_email"),
            donor_name=donor_name,
        )
    except StripeNotConfigured as exc:
        return jsonify({"error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    record_integration_event(
        current_app,
        kind="stripe_checkout",
        status="created",
        details={
            "session_id": result["session_id"],
            "org_id": org_id,
            "event_id": data.get("event_id"),
            "campaign_id": data.get("campaign_id"),
        },
    )
    return jsonify(result), 201


@integrations_bp.post("/webhooks/stripe")
def stripe_webhook_route():
    payload = request.get_data() or b""
    if len(payload) > 1024 * 1024:
        record_integration_event(current_app, kind="stripe_webhook", status="payload_too_large")
        return jsonify({"error": "Webhook payload too large."}), 413

    sig_header = request.headers.get("Stripe-Signature")
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

    if not secret:
        record_integration_event(current_app, kind="stripe_webhook", status="config_error", details={"reason": "missing_secret"})
        return jsonify({"error": "Stripe webhook secret not configured."}), 503

    event = verify_stripe_signature(payload, sig_header, secret)
    if event is None:
        record_integration_event(current_app, kind="stripe_webhook", status="invalid_signature")
        return jsonify({"error": "Invalid webhook signature."}), 400

    eid = event_id(event)
    if not eid:
        record_integration_event(current_app, kind="stripe_webhook", status="missing_event_id")
        return jsonify({"error": "Missing event id."}), 400

    if _stripe_replay_guard().is_replay(eid):
        record_integration_event(current_app, kind="stripe_webhook", status="duplicate", details={"event_id": eid})
        return jsonify({"ok": True, "status": "duplicate", "event_id": eid}), 200

    event_type = str(event.get("type") or "unknown")
    if event_type == "checkout.session.completed":
        from ngo_homesuite.services.payment_service import PaymentService, WebhookProcessingError
        session_obj = ((event.get("data") or {}).get("object") or {})
        try:
            donation = PaymentService().handle_checkout_completed(session_obj)
            metadata = session_obj.get("metadata") or {}
            raw_event_id = metadata.get("event_id")
            raw_donor_id = metadata.get("donor_id")
            if raw_event_id and raw_donor_id:
                try:
                    _upsert_event_registration_paid(
                        event_id=int(raw_event_id),
                        donor_id=int(raw_donor_id),
                        reference=str(session_obj.get("payment_intent") or session_obj.get("id") or ""),
                    )
                except Exception:
                    current_app.logger.exception("Failed to upsert paid event registration from Stripe webhook")
            details = {
                "event_id": eid,
                "event_type": event_type,
                "payment_status": session_obj.get("payment_status"),
                "session_id": session_obj.get("id"),
                "donation_id": donation.id,
                "amount": donation.amount,
                "currency": donation.currency,
            }
            record_integration_event(current_app, kind="stripe_webhook", status="processed", details=details)
            return jsonify({"ok": True, "status": "processed", "event_id": eid, "donation_id": donation.id}), 200
        except WebhookProcessingError as exc:
            current_app.logger.warning("stripe webhook processing error: %s", exc)
            record_integration_event(current_app, kind="stripe_webhook", status="error", details={"event_id": eid, "error": str(exc)})
            return jsonify({"error": str(exc)}), 422

    elif event_type == "charge.refunded":
        from ngo_homesuite.services.payment_service import PaymentService, WebhookProcessingError
        charge_obj = ((event.get("data") or {}).get("object") or {})
        try:
            donation = PaymentService().handle_charge_refunded(charge_obj)
            details = {"event_id": eid, "event_type": event_type, "donation_id": donation.id if donation else None}
            record_integration_event(current_app, kind="stripe_webhook", status="processed", details=details)
            return jsonify({"ok": True, "status": "processed", "event_id": eid}), 200
        except WebhookProcessingError as exc:
            current_app.logger.warning("stripe charge.refunded processing error: %s", exc)
            record_integration_event(current_app, kind="stripe_webhook", status="error", details={"event_id": eid, "error": str(exc)})
            return jsonify({"error": str(exc)}), 422

    elif event_type == "payment_intent.payment_failed":
        from ngo_homesuite.services.payment_service import PaymentService, WebhookProcessingError
        pi_obj = ((event.get("data") or {}).get("object") or {})
        try:
            donation = PaymentService().handle_payment_failed(pi_obj)
            details = {"event_id": eid, "event_type": event_type, "donation_id": donation.id if donation else None}
            record_integration_event(current_app, kind="stripe_webhook", status="processed", details=details)
            return jsonify({"ok": True, "status": "processed", "event_id": eid}), 200
        except WebhookProcessingError as exc:
            current_app.logger.warning("stripe payment_intent.payment_failed processing error: %s", exc)
            record_integration_event(current_app, kind="stripe_webhook", status="error", details={"event_id": eid, "error": str(exc)})
            return jsonify({"error": str(exc)}), 422

    record_integration_event(current_app, kind="stripe_webhook", status="ignored", details={"event_id": eid, "event_type": event_type})
    return jsonify({"ok": True, "status": "ignored", "event_id": eid}), 200


@integrations_bp.post("/email/webhooks/suppression")
def email_suppression_webhook_route():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    reason = str(data.get("reason") or "bounce").strip().lower()
    if not email:
        return jsonify({"error": "email is required"}), 400

    from ngo_homesuite.events.services import mark_email_bounced, mark_email_complaint
    if reason in {"complaint", "spam"}:
        mark_email_complaint(email, reason=reason)
    else:
        mark_email_bounced(email, reason=reason)
    return jsonify({"ok": True, "email": email, "reason": reason})


@integrations_bp.get("/events/reminders/opt-out/<token>")
def event_reminder_opt_out_route(token: str):
    ok = process_email_opt_out(token)
    return jsonify({"ok": ok, "status": "opted_out" if ok else "invalid_token"}), (200 if ok else 404)


@integrations_bp.get("/events/reminders/track/<kind>/<token>")
def event_reminder_track_route(kind: str, token: str):
    kind_norm = str(kind or "").strip().lower()
    if kind_norm not in {"open", "click"}:
        return jsonify({"error": "kind must be one of: open, click"}), 400

    col = "opened_at" if kind_norm == "open" else "clicked_at"
    db.session.execute(
        text(f"UPDATE event_email_queue SET {col} = :now_iso, updated_at = :now_iso WHERE opt_out_token = :token"),
        {"now_iso": _utcnow_iso(), "token": str(token or "").strip()},
    )
    db.session.commit()
    return jsonify({"ok": True, "kind": kind_norm})


@integrations_bp.post("/calendar/sync")
@login_required
@roles_required("admin", "staff")
def calendar_sync_route():
    org_id = int(current_user.organization_id)

    def _run() -> dict[str, int]:
        return sync_task_deadlines(org_id, _calendar_provider())

    try:
        result = run_with_backoff(_run, attempts=3, base_delay_seconds=0.01)
    except Exception as exc:
        record_integration_event(
            current_app,
            kind="calendar_sync",
            status="error",
            details={"organization_id": org_id, "error": str(exc)},
        )
        return jsonify({"error": "Calendar sync failed."}), 500

    record_integration_event(
        current_app,
        kind="calendar_sync",
        status="ok",
        details={"organization_id": org_id, **result},
    )
    return jsonify({"ok": True, "organization_id": org_id, **result})


@integrations_bp.post("/calendar/sync/async")
@login_required
@roles_required("admin", "staff")
def calendar_sync_async_route():
    org_id = int(current_user.organization_id)

    def _run() -> dict[str, int]:
        return sync_task_deadlines(org_id, _calendar_provider())

    job = submit_background_job(current_app, kind="calendar_sync", operation=_run)
    record_integration_event(
        current_app,
        kind="calendar_sync",
        status="async_submitted",
        details={"organization_id": org_id, "job_id": job["job_id"]},
    )
    return jsonify({"ok": True, "organization_id": org_id, "job": job}), 202


@integrations_bp.post("/calendar/caldav/sync")
@login_required
@roles_required("admin", "staff")
def caldav_sync_route():
    org_id = int(current_user.organization_id)
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", False))

    if dry_run:
        # Dry-run returns counts without mutating provider state.
        task_count = db.session.execute(
            text(
                """
                SELECT COUNT(1) AS count
                FROM tasks
                WHERE organization_id = :org_id
                  AND status IN ('open', 'in_progress')
                  AND due_date IS NOT NULL
                """
            ),
            {"org_id": org_id},
        ).scalar() or 0
        result = {"synced": int(task_count), "skipped": 0, "dry_run": True}
    else:
        try:
            result = sync_task_deadlines_to_caldav(org_id, _dav_provider())
            result["dry_run"] = False
        except Exception as exc:
            record_integration_event(
                current_app,
                kind="caldav_sync",
                status="error",
                details={"organization_id": org_id, "error": str(exc)},
            )
            return jsonify({"error": "CalDAV sync failed."}), 500

    record_integration_event(
        current_app,
        kind="caldav_sync",
        status="ok",
        details={"organization_id": org_id, **result},
    )
    return jsonify({"ok": True, "organization_id": org_id, **result}), 200


@integrations_bp.post("/contacts/carddav/sync")
@login_required
@roles_required("admin", "staff")
def carddav_sync_route():
    org_id = int(current_user.organization_id)
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", False))

    if dry_run:
        donor_count = db.session.execute(
            text(
                """
                SELECT COUNT(1) AS count
                FROM donors
                WHERE organization_id = :org_id
                  AND email IS NOT NULL
                  AND TRIM(email) != ''
                """
            ),
            {"org_id": org_id},
        ).scalar() or 0
        result = {"synced": int(donor_count), "skipped": 0, "dry_run": True}
    else:
        try:
            result = sync_donor_contacts_to_carddav(org_id, _dav_provider())
            result["dry_run"] = False
        except Exception as exc:
            record_integration_event(
                current_app,
                kind="carddav_sync",
                status="error",
                details={"organization_id": org_id, "error": str(exc)},
            )
            return jsonify({"error": "CardDAV sync failed."}), 500

    record_integration_event(
        current_app,
        kind="carddav_sync",
        status="ok",
        details={"organization_id": org_id, **result},
    )
    return jsonify({"ok": True, "organization_id": org_id, **result}), 200


@integrations_bp.get("/ops/status")
@login_required
@roles_required("admin", "staff")
def integrations_status_route():
    summary = summarize_integration_events(current_app, recent_limit=20)
    provider = _calendar_provider()
    summary["calendar_cached_events"] = len(provider.events)
    summary["organization_id"] = int(current_user.organization_id) if current_user.organization_id else None
    return jsonify(summary)


@integrations_bp.get("/ops/recent")
@login_required
@roles_required("admin", "staff")
def integrations_recent_route():
    limit = request.args.get("limit", default=25, type=int)
    limit = max(1, min(int(limit), 200))
    summary = summarize_integration_events(current_app, recent_limit=limit)
    return jsonify({"items": summary["recent"], "count": len(summary["recent"])})


@integrations_bp.get("/ops/jobs")
@login_required
@roles_required("admin", "staff")
def integrations_jobs_route():
    limit = request.args.get("limit", default=20, type=int)
    limit = max(1, min(int(limit), 200))
    jobs = list_background_jobs(current_app, limit=limit)
    return jsonify({"items": jobs, "count": len(jobs)})


@integrations_bp.get("/ops/jobs/<job_id>")
@login_required
@roles_required("admin", "staff")
def integrations_job_detail_route(job_id: str):
    job = get_background_job(current_app, job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@integrations_bp.get("/email/queue")
@login_required
@roles_required("admin", "staff")
def email_queue_status_route():
    from ngo_homesuite.utils.email_worker import list_email_queue

    rows = list_email_queue(limit=max(1, min(int(request.args.get("limit", 100)), 500)))
    html = """
    <html><head><title>Email Queue</title></head><body>
    <h1>Email Queue Status</h1>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>ID</th><th>To</th><th>Subject</th><th>Status</th><th>Attempts</th><th>Last Error</th><th>Created</th><th>Sent</th>
        </tr>
      </thead>
      <tbody>
      {% for r in rows %}
        <tr>
          <td>{{ r.id }}</td>
          <td>{{ r.to_email }}</td>
          <td>{{ r.subject }}</td>
          <td>{{ r.status }}</td>
          <td>{{ r.attempts }}</td>
          <td>{{ r.last_error or '' }}</td>
          <td>{{ r.created_at or '' }}</td>
          <td>{{ r.sent_at or '' }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    </body></html>
    """
    return render_template_string(html, rows=rows)


@integrations_bp.post("/email/smoke")
@login_required
@roles_required("admin", "staff")
def email_smoke_route():
    """Run a non-destructive email integration readiness/probe check.

    Body: {"probe": true|false}
    """
    from ngo_homesuite.utils.email import email_connectivity_smoke

    data = request.get_json(silent=True) or {}
    probe = bool(data.get("probe", False))
    result = email_connectivity_smoke(probe=probe)
    status = "ok" if bool(result.get("ready")) else "not_ready"
    record_integration_event(
        current_app,
        kind="email_smoke",
        status=status,
        details={
            "probe": probe,
            "ready": bool(result.get("ready")),
            "sendgrid_configured": bool((result.get("providers") or {}).get("sendgrid", {}).get("configured")),
            "smtp_configured": bool((result.get("providers") or {}).get("smtp", {}).get("configured")),
        },
    )
    return jsonify(result)


# ---------------------------------------------------------------------------
# SMS notifications (Twilio)
# ---------------------------------------------------------------------------

@integrations_bp.post("/sms/notify")
@login_required
@roles_required("admin", "staff")
def sms_notify_route():
    """Send a one-off SMS notification to a beneficiary or staff member.

    Body: {"to": "+15551234567", "body": "Message text"}
    """
    from ngo_homesuite.utils.sms_service import send_sms

    data = request.get_json(silent=True) or {}
    to = (data.get("to") or "").strip()
    body = (data.get("body") or "").strip()
    if not to:
        return jsonify({"error": "to is required"}), 400
    if not body:
        return jsonify({"error": "body is required"}), 400
    if not to.startswith("+"):
        return jsonify({"error": "to must be E.164 format (e.g. +15551234567)"}), 400

    ok = send_sms(to, body)
    record_integration_event(current_app, kind="sms_notify", status="ok" if ok else "failed", details={"to": to})
    return jsonify({"success": ok})


@integrations_bp.post("/sms/bulk")
@login_required
@roles_required("admin")
def sms_bulk_route():
    """Send bulk SMS to a list of recipients.

    Body: {"recipients": [{"phone": "+1...", "name": "Alice"}], "template": "Hi {name}, ..."}
    """
    from ngo_homesuite.utils.sms_service import send_bulk_sms

    data = request.get_json(silent=True) or {}
    recipients = data.get("recipients") or []
    template = (data.get("template") or "").strip()
    if not recipients:
        return jsonify({"error": "recipients is required"}), 400
    if not template:
        return jsonify({"error": "template is required"}), 400

    result = send_bulk_sms(recipients, template)
    record_integration_event(current_app, kind="sms_bulk", status="ok", details=result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Accounting: QuickBooks + Xero
# ---------------------------------------------------------------------------

@integrations_bp.post("/accounting/quickbooks/oauth/callback")
@login_required
@roles_required("admin")
def qbo_oauth_callback():
    """Exchange OAuth2 code for QuickBooks tokens.

    Body: {"code": "...", "redirect_uri": "..."}
    """
    from ngo_homesuite.services.accounting_sync_service import quickbooks_exchange_code
    from flask_login import current_user
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    redirect_uri = data.get("redirect_uri", "").strip()
    if not code or not redirect_uri:
        return jsonify({"error": "code and redirect_uri are required"}), 400
    result = quickbooks_exchange_code(current_user.organization_id, code, redirect_uri)
    if "error" in result:
        return jsonify(result), 502
    return jsonify({"status": "ok"})


@integrations_bp.post("/accounting/xero/oauth/callback")
@login_required
@roles_required("admin")
def xero_oauth_callback():
    """Exchange OAuth2 code for Xero tokens.

    Body: {"code": "...", "redirect_uri": "..."}
    """
    from ngo_homesuite.services.accounting_sync_service import xero_exchange_code
    from flask_login import current_user
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    redirect_uri = data.get("redirect_uri", "").strip()
    if not code or not redirect_uri:
        return jsonify({"error": "code and redirect_uri are required"}), 400
    result = xero_exchange_code(current_user.organization_id, code, redirect_uri)
    if "error" in result:
        return jsonify(result), 502
    return jsonify({"status": "ok"})


@integrations_bp.post("/accounting/sync/donation/<int:donation_id>")
@login_required
@roles_required("admin")
def sync_donation_route(donation_id: int):
    """Push a single donation to QuickBooks or Xero.

    Body: {"provider": "quickbooks"}  or  {"provider": "xero"}
    """
    from ngo_homesuite.services.accounting_sync_service import (
        push_donation_to_quickbooks,
        push_donation_to_xero,
    )
    from flask_login import current_user
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip().lower()
    if provider not in ("quickbooks", "xero"):
        return jsonify({"error": "provider must be 'quickbooks' or 'xero'"}), 400

    org_id = current_user.organization_id
    if provider == "quickbooks":
        result = push_donation_to_quickbooks(org_id, donation_id)
    else:
        result = push_donation_to_xero(org_id, donation_id)

    record_integration_event(current_app, kind=f"{provider}_sync", status=result.get("status", "unknown"), details=result)
    return jsonify(result)


@integrations_bp.post("/accounting/sync/expense/<int:expense_id>")
@login_required
@roles_required("admin")
def sync_expense_route(expense_id: int):
    """Push a single expense to QuickBooks or Xero.

    Body: {"provider": "quickbooks"}  or  {"provider": "xero"}
    """
    from ngo_homesuite.services.accounting_sync_service import (
        push_expense_to_quickbooks,
        push_expense_to_xero,
    )
    from flask_login import current_user
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip().lower()
    if provider not in ("quickbooks", "xero"):
        return jsonify({"error": "provider must be 'quickbooks' or 'xero'"}), 400

    org_id = current_user.organization_id
    if provider == "quickbooks":
        result = push_expense_to_quickbooks(org_id, expense_id)
    else:
        result = push_expense_to_xero(org_id, expense_id)

    record_integration_event(current_app, kind=f"{provider}_sync", status=result.get("status", "unknown"), details=result)
    return jsonify(result)


@integrations_bp.get("/accounting/sync/logs")
@login_required
@roles_required("admin")
def sync_logs_route():
    """Return recent accounting sync log entries.

    Query params: ?provider=quickbooks&sync_type=donation&status=failed&limit=50
    """
    from ngo_homesuite.services.accounting_sync_service import list_sync_logs
    from flask_login import current_user
    provider = request.args.get("provider")
    sync_type = request.args.get("sync_type")
    status = request.args.get("status")
    limit = request.args.get("limit", 100, type=int)
    logs = list_sync_logs(
        current_user.organization_id,
        provider=provider,
        sync_type=sync_type,
        status=status,
        limit=min(limit, 500),
    )
    return jsonify([
        {
            "id": l.id,
            "provider": l.provider,
            "sync_type": l.sync_type,
            "internal_id": l.internal_id,
            "external_id": l.external_id,
            "external_ref": l.external_ref,
            "status": l.status,
            "error_message": l.error_message,
            "synced_at": l.synced_at.isoformat() if l.synced_at else None,
            "created_at": l.created_at.isoformat(),
        }
        for l in logs
    ])


# ---------------------------------------------------------------------------
# Mailchimp list sync
# ---------------------------------------------------------------------------

@integrations_bp.post("/mailchimp/sync")
@login_required
@roles_required("admin")
def mailchimp_sync_route():
    """Sync current org beneficiaries (with email) to Mailchimp list.

    Optionally pass {"program": "Education"} to filter by program.
    """
    from ngo_homesuite.services.beneficiary_service import list_beneficiaries
    from ngo_homesuite.utils.mailchimp_service import sync_beneficiary_list

    data = request.get_json(silent=True) or {}
    program = data.get("program")

    org_id = int(current_user.organization_id)
    beneficiaries = list_beneficiaries(org_id, program=program, status="active")
    payload = [
        {"email": b.email, "first_name": b.first_name, "last_name": b.last_name}
        for b in beneficiaries
        if b.email
    ]
    result = sync_beneficiary_list(payload)
    record_integration_event(current_app, kind="mailchimp_sync", status="ok", details=result)
    return jsonify(result)


@integrations_bp.post("/mailchimp/subscribe")
@login_required
@roles_required("admin", "staff")
def mailchimp_subscribe_route():
    """Subscribe a single contact to the Mailchimp list.

    Body: {"email": "...", "first_name": "...", "last_name": "..."}
    """
    from ngo_homesuite.utils.mailchimp_service import add_subscriber

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "email is required"}), 400

    ok = add_subscriber(email, data.get("first_name", ""), data.get("last_name", ""))
    return jsonify({"success": ok})

