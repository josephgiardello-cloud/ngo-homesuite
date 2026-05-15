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


def _login_admin(client):
    rv = client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin123!"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)


def test_v2_grant_advance_and_disbursement_contract(client):
    _login_admin(client)

    created = client.post(
        "/api/v2/grants",
        json={
            "title": "Contract Test Grant",
            "funder_name": "Route Contract Foundation",
            "amount_requested": 1200,
        },
    )
    assert created.status_code == 201
    grant_id = created.get_json()["id"]

    missing_status = client.post(f"/api/v2/grants/{grant_id}/advance", json={})
    assert missing_status.status_code == 400

    advanced = client.post(
        f"/api/v2/grants/{grant_id}/advance",
        json={"new_status": "submitted"},
    )
    assert advanced.status_code == 200
    assert advanced.get_json()["status"] == "submitted"

    bad_date = client.post(
        f"/api/v2/grants/{grant_id}/disbursements",
        json={"amount": 500, "received_date": "15/05/2026"},
    )
    assert bad_date.status_code == 400

    disbursed = client.post(
        f"/api/v2/grants/{grant_id}/disbursements",
        json={"amount": 500, "received_date": "2026-05-15"},
    )
    assert disbursed.status_code == 201
    payload = disbursed.get_json()
    assert payload["amount"] == 500.0
    assert payload["received_date"] == "2026-05-15"


def test_v2_p2p_detail_endpoints_require_auth(client):
    page_resp = client.get("/api/v2/p2p/pages/1", follow_redirects=False)
    progress_resp = client.get("/api/v2/p2p/pages/1/progress", follow_redirects=False)
    assert page_resp.status_code in (302, 401)
    assert progress_resp.status_code in (302, 401)
