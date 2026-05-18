from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from ngo_homesuite.grants.models import Grant
from ngo_homesuite.grants.services import lifecycle as grant_service
from ngo_homesuite.models.core import db


@pytest.fixture(scope="module")
def app(shared_test_app):
    shared_test_app.config["ROLES_REQUIRING_2FA"] = []
    return shared_test_app


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


def test_grants_route_closeout_requires_approved_request(client, app):
    _login_admin(client)

    created = client.post(
        "/grants/",
        json={
            "title": "Legacy Route Grant",
            "funder_name": "Route Contract Foundation",
            "amount_requested": 1200,
        },
    )
    assert created.status_code == 201
    grant_id = created.get_json()["id"]

    submitted = client.post(f"/grants/{grant_id}/advance", json={"new_status": "submitted"})
    assert submitted.status_code == 200
    assert submitted.get_json()["status"] == "submitted"

    awarded = client.post(
        f"/grants/{grant_id}/advance",
        json={"new_status": "awarded", "amount_awarded": 1000},
    )
    assert awarded.status_code == 200
    assert awarded.get_json()["status"] == "awarded"

    disbursed = client.post(
        f"/grants/{grant_id}/disburse",
        json={"amount": 500, "received_date": "2026-05-15"},
    )
    assert disbursed.status_code == 201
    assert disbursed.get_json()["amount"] == 500.0

    close_without_approval = client.post(
        f"/grants/{grant_id}/advance",
        json={"new_status": "closed"},
    )
    assert close_without_approval.status_code == 400
    assert "approval_request_id is required" in close_without_approval.get_json()["error"]

    with app.app_context():
        grant = db.session.scalar(select(Grant).where(Grant.id == grant_id))
        assert grant is not None
        grant_service.add_disbursement(grant_id, grant.organization_id, 500.0, date(2026, 5, 16))

        close_req = grant_service.create_approval_request(
            grant.organization_id,
            action_type="grant_closeout",
            resource_type="grant",
            resource_id=grant_id,
            requested_by_user_id=101,
            requested_by_role="staff",
        )
        grant_service.decide_approval_request(
            close_req.id,
            grant.organization_id,
            decided_by_user_id=202,
            decided_by_role="org_admin",
            decision="approved",
        )
        grant_service.decide_approval_request(
            close_req.id,
            grant.organization_id,
            decided_by_user_id=203,
            decided_by_role="controller",
            decision="approved",
        )

    closed = client.post(
        f"/grants/{grant_id}/advance",
        json={"new_status": "closed", "approval_request_id": close_req.id},
    )
    assert closed.status_code == 200
    assert closed.get_json()["status"] == "closed"


def test_grants_workbench_page_renders_for_authenticated_user(client):
    _login_admin(client)

    rv = client.get("/grants/workbench")
    assert rv.status_code == 200
    assert b"Grant Intelligence Workbench" in rv.data