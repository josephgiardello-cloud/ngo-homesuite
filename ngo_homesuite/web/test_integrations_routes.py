from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from ngo_homesuite.models.core import Organization, Task, User, db


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



def test_ops_routes_require_authentication(client):
    rv = client.get("/integrations/ops/status")
    assert rv.status_code in {302, 401, 403}
