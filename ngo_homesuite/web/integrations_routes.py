from __future__ import annotations

import json
import os

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.services.calendar_sync_service import InMemoryCalendarProvider, sync_task_deadlines
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


@integrations_bp.post("/webhooks/stripe")
def stripe_webhook_route():
    payload = request.get_data() or b""
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
    if _stripe_replay_guard().is_replay(eid):
        record_integration_event(current_app, kind="stripe_webhook", status="duplicate", details={"event_id": eid})
        return jsonify({"ok": True, "status": "duplicate", "event_id": eid}), 200

    event_type = str(event.get("type") or "unknown")
    if event_type == "checkout.session.completed":
        session_obj = ((event.get("data") or {}).get("object") or {})
        details = {
            "event_id": eid,
            "event_type": event_type,
            "payment_status": session_obj.get("payment_status"),
            "session_id": session_obj.get("id"),
        }
        record_integration_event(current_app, kind="stripe_webhook", status="processed", details=details)
        return jsonify({"ok": True, "status": "processed", "event_id": eid}), 200

    record_integration_event(current_app, kind="stripe_webhook", status="ignored", details={"event_id": eid, "event_type": event_type})
    return jsonify({"ok": True, "status": "ignored", "event_id": eid}), 200


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

