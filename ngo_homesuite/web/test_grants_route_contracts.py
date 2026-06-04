from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from ngo_homesuite.grants.models import Grant
from ngo_homesuite.grants.services import lifecycle as grant_service
from ngo_homesuite.models.core import db


@pytest.fixture(scope="module")
def app(shared_test_app):
    original_roles_requiring_2fa = shared_test_app.config.get("ROLES_REQUIRING_2FA")
    shared_test_app.config["ROLES_REQUIRING_2FA"] = []
    try:
        yield shared_test_app
    finally:
        shared_test_app.config["ROLES_REQUIRING_2FA"] = original_roles_requiring_2fa


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


def test_grant_intelligence_workbench_page_renders_for_authenticated_user(client):
    _login_admin(client)

    rv = client.get("/admin/grants/intelligence")
    assert rv.status_code == 200
    assert b"Grant Intelligence Workbench" in rv.data


def test_grant_readiness_route_returns_score(client, app):
    _login_admin(client)

    created = client.post(
        "/grants/",
        json={
            "title": "Readiness Route Grant",
            "funder_name": "Readiness Foundation",
            "amount_requested": 2000,
        },
    )
    assert created.status_code == 201
    grant_id = created.get_json()["id"]

    with app.app_context():
        grant = db.session.scalar(select(Grant).where(Grant.id == grant_id))
        assert grant is not None
        grant.description = "This grant opportunity funds a community program with clear outcomes and reporting requirements."
        grant.application_deadline = date(2026, 6, 30)
        grant.report_due_date = date(2026, 12, 31)
        grant.requirements = "Quarterly reporting and measurable outcomes are required."
        db.session.commit()

    readiness_resp = client.get(f"/admin/compliance/grant/{grant_id}/readiness")
    assert readiness_resp.status_code == 200
    readiness_payload = readiness_resp.get_json()
    assert "readiness_score" in readiness_payload
    assert "status" in readiness_payload


def test_grant_opportunity_search_route_returns_matches(client, monkeypatch):
    _login_admin(client)

    def fake_search(org_id, *args, **kwargs):
        return [
            {
                "external_source": "grants_gov",
                "external_opportunity_id": "GS-3001",
                "title": "Community Health Grant",
                "funder_name": "Community Health Agency",
                "program_name": "Health Access",
                "deadline": date(2026, 7, 1),
                "amount_min": 10000,
                "amount_max": 50000,
                "external_url": "https://example.com/grant",
                "applicability_score": 93.5,
                "match_reasons": ["Keyword overlap: health"],
            }
        ]

    monkeypatch.setattr(
        "ngo_homesuite.grants.services.facade.GrantsFacade.search_grants_gov_opportunities",
        fake_search,
    )

    search_resp = client.get(
        "/admin/grants/opportunities/search?q=health&applicant_profile=community&requested_amount=25000"
    )
    assert search_resp.status_code == 200
    search_payload = search_resp.get_json()
    assert search_payload["count"] == 1
    assert search_payload["results"][0]["title"] == "Community Health Grant"