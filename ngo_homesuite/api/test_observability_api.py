from __future__ import annotations

import re

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Organization, User, db


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        COPILOT_ENABLED = True
        METRICS_ENABLED = True
        ROLES_REQUIRING_2FA = []

    app = create_app(_TestCfg)

    @app.get("/_test/observability/fail", endpoint="observability_fail")
    def observability_fail():
        return {"error": "synthetic release-lane failure"}, 500

    return app


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


def _iter_metric_samples(body: str):
    pattern = re.compile(
        r'^(?P<name>[^{\s]+)(?:\{(?P<labels>[^}]*)\})?(?P<suffix>_[A-Za-z0-9_]+)?\s+(?P<value>-?\d+(?:\.\d+)?)$'
    )
    for line in body.splitlines():
        match = pattern.match(line.strip())
        if match is None:
            continue
        labels: dict[str, str] = {}
        raw_labels = match.group("labels") or ""
        if raw_labels:
            for item in raw_labels.split(","):
                key, raw_value = item.split("=", 1)
                labels[key] = raw_value.strip().strip('"')
        yield match.group("name") + (match.group("suffix") or ""), labels, float(match.group("value"))


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


def test_5xx_requests_emit_metrics_and_satisfy_http_error_alert_ratio(client, app):
    _ensure_user(app, "obs_release_admin", "obs_release_admin@test.local", "admin", "obs_release_admin_pass_123")
    _login(client, "obs_release_admin", "obs_release_admin_pass_123")

    healthy = client.get("/api/v1/workflows", headers={"X-Request-ID": "obs-release-ok"})
    assert healthy.status_code == 200

    failing = client.get("/_test/observability/fail", headers={"X-Request-ID": "obs-release-500"})
    assert failing.status_code == 500
    assert failing.get_json() == {"error": "synthetic release-lane failure"}

    metrics = client.get("/api/v1/metrics")
    assert metrics.status_code == 200
    body = metrics.get_data(as_text=True)

    samples = list(_iter_metric_samples(body))
    failing_http = [
        value
        for name, labels, value in samples
        if name == "http_requests_total"
        and labels.get("endpoint") == "observability_fail"
        and labels.get("method") == "GET"
        and labels.get("status") == "500"
    ]
    assert failing_http == [1.0]

    failing_latency_counts = [
        value
        for name, labels, value in samples
        if name == "http_request_latency_ms_count"
        and labels.get("endpoint") == "observability_fail"
        and labels.get("method") == "GET"
        and labels.get("status") == "500"
    ]
    assert failing_latency_counts == [1.0]

    total_http = sum(value for name, _, value in samples if name == "http_requests_total")
    total_5xx = sum(
        value
        for name, labels, value in samples
        if name == "http_requests_total" and re.fullmatch(r"5\d\d", labels.get("status", ""))
    )
    assert total_http >= 2.0
    assert total_5xx == 1.0
    assert total_5xx / max(total_http, 0.001) > 0.05
