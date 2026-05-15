from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Organization, User, db


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        COPILOT_ENABLED = True
        METRICS_ENABLED = True

    return create_app(_TestCfg)


@pytest.fixture()
def client(app):
    return app.test_client()


def _ensure_user(app, username: str, email: str, role: str, password: str) -> None:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        org = Organization.query.filter_by(is_active=True).first()
        if user is None:
            user = User(
                username=username,
                email=email,
                role=role,
                is_active=True,
                organization_id=org.id if org else None,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def test_request_id_header_and_metrics_endpoint(client, app):
    _ensure_user(app, "obs_admin", "obs_admin@test.local", "admin", "obs_admin_pass_123")
    _login(client, "obs_admin", "obs_admin_pass_123")

    req_id = "req-test-123"
    workflows = client.get("/api/v1/workflows", headers={"X-Request-ID": req_id})
    assert workflows.status_code == 200
    assert workflows.headers.get("X-Request-ID") == req_id

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        org_id = str(org.id)

    created = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": org_id, "workflow_type": "case_intake"},
    )
    assert created.status_code == 200
    instance_id = created.get_json()["instance"]["instance_id"]

    transitioned = client.post(
        f"/api/v1/workflows/instances/{instance_id}/events",
        json={
            "org_id": org_id,
            "event_type": "intake_submit",
            "payload": {"case_id": "OBS-001"},
        },
    )
    assert transitioned.status_code == 200

    metrics = client.get("/api/v1/metrics")
    assert metrics.status_code == 200
    body = metrics.get_data(as_text=True)
    assert "http_requests_total" in body
    assert "http_request_latency_ms{" in body
    assert "_sum" in body
    assert "workflow_events_total{event_type=\"intake_submit\",workflow_type=\"case_intake\"}" in body


def test_metrics_requires_org_admin(client, app):
    _ensure_user(app, "obs_viewer", "obs_viewer@test.local", "viewer", "obs_viewer_pass_123")
    _login(client, "obs_viewer", "obs_viewer_pass_123")

    metrics = client.get("/api/v1/metrics")
    assert metrics.status_code == 403
