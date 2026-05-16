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
    assert rv.get_data(as_text=True) == "ok"
