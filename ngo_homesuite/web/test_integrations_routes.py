from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from ngo_homesuite.models.core import Donation, Donor, Organization, Task, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()



def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})



def _ensure_user(app, username: str, email: str, role: str, password: str) -> int:
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        if org is None:
            org = Organization(name="Integrations Org", slug="integrations-org", is_active=True)
            db.session.add(org)
            db.session.flush()

        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, email=email, role=role, is_active=True, organization_id=org.id)
            user.set_password(password)
            db.session.add(user)
        else:
            user.organization_id = org.id
        db.session.commit()
        return int(org.id)



def _stripe_header(payload: bytes, secret: str, ts: int) -> str:
    signed = f"{ts}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"



def test_stripe_webhook_processed_and_duplicate(client, app, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

    # Ensure an org exists and obtain its ID for the metadata
    org_id = _ensure_user(app, "webhook_admin", "webhook_admin@test.local", "admin", "webhook_admin_pass_123")

    payload = {
        "id": "evt_webhook_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_123",
                "payment_status": "paid",
                "payment_intent": "pi_test_dedup_1",
                "amount_total": 5000,
                "currency": "usd",
                "customer_details": {"name": "Test Donor", "email": "donor@test.local"},
                "metadata": {"org_id": str(org_id)},
            }
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = _stripe_header(raw, "whsec_test", int(time.time()))

    rv1 = client.post(
        "/integrations/webhooks/stripe",
        data=raw,
        headers={"Stripe-Signature": header},
    )
    assert rv1.status_code == 200
    body1 = rv1.get_json()
    assert body1["status"] == "processed"
    assert "donation_id" in body1

    rv2 = client.post(
        "/integrations/webhooks/stripe",
        data=raw,
        headers={"Stripe-Signature": header},
    )
    assert rv2.status_code == 200
    assert rv2.get_json()["status"] == "duplicate"



def test_stripe_webhook_rejects_invalid_signature(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    rv = client.post(
        "/integrations/webhooks/stripe",
        data=b'{"id":"evt_bad","type":"checkout.session.completed"}',
        headers={"Stripe-Signature": "t=1700000000,v1=deadbeef"},
    )
    assert rv.status_code == 400


def test_stripe_webhook_completes_existing_pending_donation(client, app, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    org_id = _ensure_user(app, "pending_donation_admin", "pending_donation_admin@test.local", "admin", "pending_donation_admin_pass_123")

    with app.app_context():
        donor = Donor(organization_id=org_id, name="Pending Donor", email="pending@example.org")
        db.session.add(donor)
        db.session.flush()

        donation = Donation(
            organization_id=org_id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=15.0,
            currency="USD",
            status="pending",
            payment_method="stripe",
        )
        db.session.add(donation)
        db.session.commit()
        donation_id = int(donation.id)

    payload = {
        "id": "evt_webhook_pending_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_pending_1",
                "payment_status": "paid",
                "payment_intent": "pi_pending_1",
                "amount_total": 1500,
                "currency": "usd",
                "customer_details": {"name": "Pending Donor", "email": "pending@example.org"},
                "metadata": {"org_id": str(org_id), "donation_id": str(donation_id)},
            }
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = _stripe_header(raw, "whsec_test", int(time.time()))

    rv = client.post("/integrations/webhooks/stripe", data=raw, headers={"Stripe-Signature": header})
    assert rv.status_code == 200

    with app.app_context():
        refreshed = db.session.get(Donation, donation_id)
        assert refreshed is not None
        assert refreshed.status == "received"
        assert refreshed.reference_number == "pi_pending_1"


def test_stripe_webhook_rejects_missing_event_id(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

    payload = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_missing_event_id", "payment_status": "paid", "amount_total": 1000, "currency": "usd", "metadata": {"org_id": "1"}}},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = _stripe_header(raw, "whsec_test", int(time.time()))

    rv = client.post(
        "/integrations/webhooks/stripe",
        data=raw,
        headers={"Stripe-Signature": header},
    )
    assert rv.status_code == 400
    assert "event id" in (rv.get_json() or {}).get("error", "").lower()


def test_stripe_webhook_rejects_stale_timestamp(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

    payload = {
        "id": "evt_stale_1",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_stale_1", "payment_status": "paid", "amount_total": 5000, "currency": "usd", "metadata": {"org_id": "1"}}},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    stale_ts = int(time.time()) - 1000
    header = _stripe_header(raw, "whsec_test", stale_ts)

    rv = client.post(
        "/integrations/webhooks/stripe",
        data=raw,
        headers={"Stripe-Signature": header},
    )
    assert rv.status_code == 400



def test_calendar_sync_and_ops_routes(client, app):
    org_id = _ensure_user(app, "integration_staff", "integration_staff@test.local", "staff", "integration_staff_pass_123")
    _login(client, "integration_staff", "integration_staff_pass_123")

    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        task = Task(
            organization_id=org_id,
            title="Calendar sync task",
            status="open",
            priority="high",
            due_date=now + timedelta(days=1),
        )
        db.session.add(task)
        db.session.commit()

    rv = client.post("/integrations/calendar/sync")
    assert rv.status_code == 200
    payload = rv.get_json()
    assert payload["ok"] is True
    assert payload["synced"] >= 1

    status_rv = client.get("/integrations/ops/status")
    assert status_rv.status_code == 200
    status_payload = status_rv.get_json()
    assert "total_events" in status_payload
    assert "calendar_sync" in status_payload["by_kind"]

    recent_rv = client.get("/integrations/ops/recent?limit=5")
    assert recent_rv.status_code == 200
    recent_payload = recent_rv.get_json()
    assert recent_payload["count"] >= 1

    async_rv = client.post("/integrations/calendar/sync/async")
    assert async_rv.status_code == 202
    async_payload = async_rv.get_json()
    assert async_payload["ok"] is True
    assert async_payload["job"]["status"] in {"completed", "running", "queued"}

    job_id = async_payload["job"]["job_id"]
    job_rv = client.get(f"/integrations/ops/jobs/{job_id}")
    assert job_rv.status_code == 200
    assert job_rv.get_json()["job_id"] == job_id

    jobs_rv = client.get("/integrations/ops/jobs?limit=10")
    assert jobs_rv.status_code == 200
    jobs_payload = jobs_rv.get_json()
    assert jobs_payload["count"] >= 1


def test_caldav_and_carddav_sync_routes(client, app):
    org_id = _ensure_user(app, "dav_staff", "dav_staff@test.local", "staff", "dav_staff_pass_123")
    _login(client, "dav_staff", "dav_staff_pass_123")

    app.extensions.pop("dav_sync_provider", None)

    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        task = Task(
            organization_id=org_id,
            title="DAV due task",
            status="open",
            priority="medium",
            due_date=now + timedelta(days=2),
        )
        donor = Donor(
            organization_id=org_id,
            name="DAV Contact",
            email="dav.contact@example.org",
            phone="+15550101010",
            donor_type="individual",
        )
        db.session.add_all([task, donor])
        db.session.commit()

    caldav_rv = client.post("/integrations/calendar/caldav/sync", json={})
    assert caldav_rv.status_code == 200
    caldav_payload = caldav_rv.get_json()
    assert caldav_payload["ok"] is True
    assert caldav_payload["dry_run"] is False
    assert int(caldav_payload["synced"]) >= 0
    assert int(caldav_payload["skipped"]) >= 0

    carddav_rv = client.post("/integrations/contacts/carddav/sync", json={})
    assert carddav_rv.status_code == 200
    carddav_payload = carddav_rv.get_json()
    assert carddav_payload["ok"] is True
    assert carddav_payload["dry_run"] is False
    assert int(carddav_payload["synced"]) >= 0
    assert int(carddav_payload["skipped"]) >= 0

    provider = app.extensions.get("dav_sync_provider")
    assert provider is not None
    assert hasattr(provider, "caldav_events")
    assert hasattr(provider, "carddav_contacts")

    dry_caldav = client.post("/integrations/calendar/caldav/sync", json={"dry_run": True})
    assert dry_caldav.status_code == 200
    assert dry_caldav.get_json()["dry_run"] is True
    assert int(dry_caldav.get_json()["synced"]) >= 0

    dry_carddav = client.post("/integrations/contacts/carddav/sync", json={"dry_run": True})
    assert dry_carddav.status_code == 200
    assert dry_carddav.get_json()["dry_run"] is True
    assert int(dry_carddav.get_json()["synced"]) >= 0

    status_rv = client.get("/integrations/ops/status")
    assert status_rv.status_code == 200
    status_payload = status_rv.get_json()
    assert "caldav_sync" in status_payload["by_kind"]
    assert "carddav_sync" in status_payload["by_kind"]

    capabilities_rv = client.get("/integrations/dav/capabilities")
    assert capabilities_rv.status_code == 200
    capabilities_payload = capabilities_rv.get_json() or {}
    assert capabilities_payload.get("ok") is True
    assert isinstance((capabilities_payload.get("capabilities") or {}).get("carddav_sync"), bool)
    assert isinstance((capabilities_payload.get("provider_state") or {}).get("carddav_contacts"), int)



def test_ops_routes_require_authentication(client):
    rv = client.get("/integrations/ops/status")
    assert rv.status_code in {302, 401, 403}


def test_email_smoke_requires_authentication(client):
    rv = client.post("/integrations/email/smoke", json={"probe": False})
    assert rv.status_code in {302, 401, 403}


def test_email_smoke_returns_readiness(client, app):
    _ensure_user(app, "email_smoke_staff", "email_smoke_staff@test.local", "staff", "email_smoke_staff_pass_123")
    _login(client, "email_smoke_staff", "email_smoke_staff_pass_123")

    with mock.patch("ngo_homesuite.utils.email.email_connectivity_smoke", return_value={
        "probe": False,
        "ready": True,
        "providers": {
            "sendgrid": {"configured": False, "probed": False, "ok": None, "error": None},
            "smtp": {"configured": True, "probed": False, "ok": None, "error": None, "host": "smtp.example.org", "port": 587, "use_tls": True},
        },
    }):
        rv = client.post("/integrations/email/smoke", json={"probe": False})

    assert rv.status_code == 200
    payload = rv.get_json()
    assert payload["ready"] is True
    assert payload["providers"]["smtp"]["configured"] is True


def test_email_smoke_probe_mode_passes_flag(client, app):
    _ensure_user(app, "email_smoke_admin", "email_smoke_admin@test.local", "staff", "email_smoke_admin_pass_123")
    _login(client, "email_smoke_admin", "email_smoke_admin_pass_123")

    with mock.patch("ngo_homesuite.utils.email.email_connectivity_smoke", return_value={
        "probe": True,
        "ready": False,
        "providers": {
            "sendgrid": {"configured": True, "probed": True, "ok": False, "error": "sendgrid_http_401"},
            "smtp": {"configured": False, "probed": False, "ok": None, "error": None, "host": None, "port": 25, "use_tls": False},
        },
    }) as smoke_mock:
        rv = client.post("/integrations/email/smoke", json={"probe": True})

    assert rv.status_code == 200
    smoke_mock.assert_called_once_with(probe=True)
    payload = rv.get_json()
    assert payload["probe"] is True
    assert payload["ready"] is False


def test_email_queue_status_page_renders_table(client, app):
    _ensure_user(app, "email_queue_admin", "email_queue_admin@test.local", "staff", "email_queue_admin_pass_123")
    _login(client, "email_queue_admin", "email_queue_admin_pass_123")

    with app.app_context():
        db.session.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS email_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    to_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT,
                    sent_at TEXT
                )
                """
            )
        )
        db.session.execute(
            db.text(
                "INSERT INTO email_queue(to_email, subject, body, status, attempts) VALUES ('q@example.org','Queued','Body','pending',0)"
            )
        )
        db.session.commit()

    rv = client.get("/integrations/email/queue")
    assert rv.status_code == 200
    assert b"Email Queue Status" in rv.data
    assert b"q@example.org" in rv.data
