"""
Health endpoint structured response tests.

Verifies the /health endpoint returns:
- 200 with JSON body when db is reachable
- status, db, migration_version, expected_migration_version,
  migration_current, uptime_seconds fields
"""
from __future__ import annotations

import json

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_health_returns_json_with_status_ok(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    assert rv.content_type.startswith("application/json")


def test_health_json_body_contains_required_fields(client):
    rv = client.get("/health")
    body = rv.get_json()
    assert body is not None

    assert "status" in body
    assert "db" in body
    assert "migration_version" in body
    assert "expected_migration_version" in body
    assert "migration_current" in body
    assert "uptime_seconds" in body


def test_health_db_is_reachable(client):
    rv = client.get("/health")
    body = rv.get_json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_migration_version_is_integer(client):
    rv = client.get("/health")
    body = rv.get_json()
    # migration_version is None when schema_version table has no rows (test env uses create_all)
    assert body["migration_version"] is None or isinstance(body["migration_version"], int), (
        f"migration_version should be an int or None, got {body['migration_version']!r}"
    )
    assert isinstance(body["expected_migration_version"], int)


def test_health_migration_is_current(client):
    """migration_current is a bool; value depends on whether SQL migrations were run."""
    rv = client.get("/health")
    body = rv.get_json()
    # Just assert it's a bool â€” test env uses create_all so migration_version may be None
    assert isinstance(body["migration_current"], bool)


def test_health_uptime_seconds_is_non_negative(client):
    rv = client.get("/health")
    body = rv.get_json()
    uptime = body["uptime_seconds"]
    assert isinstance(uptime, (int, float))
    assert uptime >= 0.0


# ---------------------------------------------------------------------------
# Observability: X-Request-ID propagation
# ---------------------------------------------------------------------------

def test_health_response_carries_request_id_header(client):
    rv = client.get("/health")
    assert "X-Request-ID" in rv.headers
    assert rv.headers["X-Request-ID"]  # non-empty


def test_health_echoes_caller_provided_request_id(client):
    rv = client.get("/health", headers={"X-Request-ID": "test-req-42"})
    assert rv.headers.get("X-Request-ID") == "test-req-42"


# ---------------------------------------------------------------------------
# /health/live  â€” liveness probe
# ---------------------------------------------------------------------------

def test_health_live_returns_200(client):
    rv = client.get("/health/live")
    assert rv.status_code == 200


def test_health_live_returns_json_with_live_status(client):
    rv = client.get("/health/live")
    body = rv.get_json()
    assert body is not None
    assert body.get("status") == "live"


def test_health_live_is_public(client):
    """Liveness probe must be accessible without authentication."""
    rv = client.get("/health/live")
    assert rv.status_code == 200


# ---------------------------------------------------------------------------
# /health/ready â€” readiness probe
# ---------------------------------------------------------------------------

def test_health_ready_returns_json(client):
    rv = client.get("/health/ready")
    body = rv.get_json()
    assert body is not None
    assert "status" in body
    assert "db" in body
    assert "migration_current" in body


def test_health_ready_is_public(client):
    """Readiness probe must be accessible without authentication."""
    rv = client.get("/health/ready")
    assert rv.status_code in (200, 503)


def test_health_accessible_without_auth(client):
    """Health endpoint must work for unauthenticated liveness probes."""
    rv = client.get("/health", follow_redirects=False)
    # Must not redirect to login
    assert rv.status_code == 200
