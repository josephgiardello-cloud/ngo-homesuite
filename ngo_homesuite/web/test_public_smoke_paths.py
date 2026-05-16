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


def test_public_p2p_leaderboard_smoke(client):
    rv = client.get("/p2p/leaderboard")
    assert rv.status_code == 302
    assert "/auth/login" in (rv.headers.get("Location") or "")
