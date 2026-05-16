from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Donation, Donor, MembershipTier, Organization, ProgramCase, Task, db


@pytest.fixture(scope="module")
def app(shared_test_app):
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


def test_v2_p2p_link_rejects_cross_tenant_donation(client, app):
    _login_admin(client)

    create_page_resp = client.post(
        "/api/v2/p2p/pages",
        json={"donor_id": 1, "title": "Tenant Safety Page"},
    )
    assert create_page_resp.status_code == 201
    page_id = create_page_resp.get_json()["id"]

    with app.app_context():
        org2 = Organization(name="Route Org Two", slug="route-org-two", is_active=True)
        db.session.add(org2)
        db.session.flush()

        donor2 = Donor(organization_id=org2.id, name="Org2 Donor", email="org2.donor@example.org")
        db.session.add(donor2)
        db.session.flush()

        donation2 = Donation(
            organization_id=org2.id,
            donor_id=donor2.id,
            donor_name=donor2.name,
            donor_email=donor2.email,
            amount=25.0,
            currency="USD",
            status="received",
            payment_method="bank_transfer",
        )
        db.session.add(donation2)
        db.session.commit()
        foreign_donation_id = donation2.id

    rv = client.post(
        f"/api/v2/p2p/pages/{page_id}/link-donation",
        json={"donation_id": foreign_donation_id},
    )
    assert rv.status_code == 400
    assert (rv.get_json() or {}).get("error") == "Invalid resource reference"


def test_v2_p2p_create_rejects_invalid_goal_input(client):
    _login_admin(client)

    rv = client.post(
        "/api/v2/p2p/pages",
        json={"donor_id": 1, "title": "Invalid Goal Page", "goal_amount": -5},
    )
    assert rv.status_code == 400


def test_v2_mutating_endpoints_reject_cross_tenant_references(client, app):
    _login_admin(client)

    with app.app_context():
        local_org = db.session.scalars(
            db.select(Organization).where(Organization.is_active == True).order_by(Organization.id.asc()).limit(1)
        ).first()
        assert local_org is not None
        local_org_id = int(local_org.id)

        local_donor = db.session.scalars(
            db.select(Donor).where(Donor.organization_id == local_org_id).limit(1)
        ).first()
        if local_donor is None:
            local_donor = Donor(
                organization_id=local_org_id,
                name="Tenant Matrix Local Donor",
                email="tenant-matrix-local@example.org",
            )
            db.session.add(local_donor)
            db.session.flush()

        foreign_org = Organization(name="Tenant Matrix Org", slug="tenant-matrix-org", is_active=True)
        db.session.add(foreign_org)
        db.session.flush()

        foreign_donor = Donor(
            organization_id=foreign_org.id,
            name="Tenant Matrix Foreign Donor",
            email="tenant-matrix-foreign@example.org",
        )
        db.session.add(foreign_donor)
        db.session.flush()

        foreign_tier = MembershipTier(
            organization_id=foreign_org.id,
            name="Foreign Tier",
            price=10,
            currency="USD",
            interval="annual",
        )
        db.session.add(foreign_tier)
        db.session.flush()

        foreign_task = Task(
            organization_id=foreign_org.id,
            title="Foreign Task",
            status="open",
            priority="medium",
            task_type="general",
        )
        db.session.add(foreign_task)
        db.session.flush()

        foreign_case = ProgramCase(
            organization_id=foreign_org.id,
            title="Foreign Case",
            case_type="service",
            status="open",
        )
        db.session.add(foreign_case)
        db.session.commit()

        foreign_donor_id = int(foreign_donor.id)
        foreign_tier_id = int(foreign_tier.id)
        foreign_task_id = int(foreign_task.id)
        foreign_case_id = int(foreign_case.id)
        local_donor_id = int(local_donor.id)

    create_page_with_foreign_donor = client.post(
        "/api/v2/p2p/pages",
        json={"donor_id": foreign_donor_id, "title": "Cross Tenant Page"},
    )
    assert create_page_with_foreign_donor.status_code == 400
    assert (create_page_with_foreign_donor.get_json() or {}).get("error") == "Invalid resource reference"

    enroll_with_foreign_tier = client.post(
        "/api/v2/membership/enroll",
        json={"donor_id": local_donor_id, "tier_id": foreign_tier_id},
    )
    assert enroll_with_foreign_tier.status_code == 404

    complete_foreign_task = client.post(f"/api/v2/tasks/{foreign_task_id}/complete", json={"notes": "x"})
    assert complete_foreign_task.status_code == 404

    advance_foreign_case = client.post(
        f"/api/v2/cases/{foreign_case_id}/status",
        json={"new_status": "closed"},
    )
    assert advance_foreign_case.status_code == 404


def test_v2_activity_feed_and_insights_contract(client):
    _login_admin(client)

    created = client.post(
        "/api/v2/tasks",
        json={"title": "Timeline contract task", "priority": "high"},
    )
    assert created.status_code == 201

    feed = client.get("/api/v2/activity/global?limit=20&entity_type=donor&q=donation")
    assert feed.status_code == 200
    feed_payload = feed.get_json()
    assert isinstance(feed_payload, list)

    unfiltered = client.get("/api/v2/activity/global?limit=20")
    assert unfiltered.status_code == 200
    unfiltered_payload = unfiltered.get_json()
    assert isinstance(unfiltered_payload, list)
    assert unfiltered_payload

    first = unfiltered_payload[0]
    assert isinstance(first.get("metadata"), dict)
    assert "activity_id" in first
    assert "entity_type" in first
    assert "entity_id" in first

    insights = client.get("/api/v2/activity/insights?limit=20&entity_type=donor")
    assert insights.status_code == 200
    payload = insights.get_json()
    assert isinstance(payload, dict)
    assert isinstance(payload.get("summary"), str)
    assert isinstance(payload.get("next_best_action"), str)
    assert isinstance(payload.get("recommended_actions"), list)


def test_v2_task_board_and_reminder_candidates_contract(client):
    _login_admin(client)

    created = client.post(
        "/api/v2/tasks",
        json={"title": "Board contract task", "priority": "high"},
    )
    assert created.status_code == 201

    board = client.get("/api/v2/tasks/board")
    assert board.status_code == 200
    payload = board.get_json()
    assert isinstance(payload, dict)
    assert isinstance(payload.get("summary"), dict)
    assert isinstance(payload.get("tasks"), list)
    assert isinstance(payload.get("reminder_candidates"), list)

    candidates = client.get("/api/v2/tasks/reminder-candidates?limit=15")
    assert candidates.status_code == 200
    assert isinstance(candidates.get_json(), list)
