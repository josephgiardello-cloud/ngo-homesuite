from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_health_endpoint_is_public_and_ok(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body is not None
    assert body.get("status") in ("ok", "degraded")
    assert "db" in body
    assert "migration_version" in body
