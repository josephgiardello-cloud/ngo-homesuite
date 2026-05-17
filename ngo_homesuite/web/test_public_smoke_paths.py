from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig


@pytest.fixture(scope="module")
def app():
    return create_app(TestingConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


def test_public_health_smoke(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body is not None
    assert body.get("status") in ("ok", "degraded")
    assert "db" in body
    assert "migration_version" in body
    assert "uptime_seconds" in body


def test_public_give_page_smoke(client):
    rv = client.get("/give")
    assert rv.status_code == 200
    assert b"Donate" in rv.data or b"donate" in rv.data


def test_public_stripe_donate_page_smoke(client):
    rv = client.get("/public/donate")
    assert rv.status_code == 200
    assert b"Donate with Stripe" in rv.data


def test_public_p2p_leaderboard_smoke(client):
    rv = client.get("/p2p/leaderboard")
    assert rv.status_code == 302
    assert "/auth/login" in (rv.headers.get("Location") or "")


# ---------------------------------------------------------------------------
# Auth pages are publicly accessible
# ---------------------------------------------------------------------------

def test_public_login_page_renders(client):
    rv = client.get("/auth/login")
    assert rv.status_code == 200
    assert b"login" in rv.data.lower() or b"sign in" in rv.data.lower()


def test_public_register_page_renders(client):
    rv = client.get("/auth/register")
    assert rv.status_code == 200
    assert b"register" in rv.data.lower() or b"create" in rv.data.lower() or b"account" in rv.data.lower()


# ---------------------------------------------------------------------------
# Protected routes redirect unauthenticated requests to login
# ---------------------------------------------------------------------------

def test_unauthenticated_dashboard_redirects_to_login(client):
    rv = client.get("/dashboard", follow_redirects=False)
    assert rv.status_code == 302
    assert "/auth/login" in (rv.headers.get("Location") or "")


def test_unauthenticated_donors_list_redirects_to_login(client):
    rv = client.get("/donors", follow_redirects=False)
    assert rv.status_code == 302
    assert "/auth/login" in (rv.headers.get("Location") or "")


def test_unauthenticated_reports_redirects_to_login(client):
    rv = client.get("/reports", follow_redirects=False)
    assert rv.status_code == 302
    assert "/auth/login" in (rv.headers.get("Location") or "")


def test_unauthenticated_campaign_email_workbench_redirects_to_login(client):
    rv = client.get("/campaigns/email-workbench", follow_redirects=False)
    assert rv.status_code == 302
    assert "/auth/login" in (rv.headers.get("Location") or "")


def test_unauthenticated_api_v1_metrics_requires_auth(client):
    rv = client.get("/api/v1/metrics", follow_redirects=False)
    # Either 401/403 (JSON API style) or 302 redirect to login
    assert rv.status_code in (302, 401, 403)
