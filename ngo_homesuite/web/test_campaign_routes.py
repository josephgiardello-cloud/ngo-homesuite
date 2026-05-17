"""Tests for campaign V2 API routes."""
from __future__ import annotations

import unittest.mock as mock

import pytest
from sqlalchemy import select

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import (
    Campaign,
    CampaignEmailBatch,
    CampaignEmailDelivery,
    Donation,
    Donor,
    Organization,
    User,
    db,
)


@pytest.fixture(scope="module")
def app():
    class _Cfg(TestingConfig):
        SECRET_KEY = "test-campaign"

    return create_app(_Cfg)


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


def _login_staff(client):
    rv = client.post(
        "/auth/login",
        data={"username": "staff", "password": "staff123!"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)


def _login_viewer(client):
    rv = client.post(
        "/auth/login",
        data={"username": "viewer", "password": "viewer123!"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)


def _admin_org_id(app) -> int:
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        assert user is not None
        assert user.organization_id is not None
        return int(user.organization_id)


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------

def test_list_campaigns_requires_login(client):
    rv = client.get("/api/v2/campaigns")
    assert rv.status_code in (302, 401)


def test_list_campaigns_empty_for_fresh_org(client, app):
    _login_admin(client)
    rv = client.get("/api/v2/campaigns")
    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------

def test_create_campaign_missing_name_returns_400(client):
    _login_admin(client)
    rv = client.post("/api/v2/campaigns", json={})
    assert rv.status_code == 400


def test_create_campaign_success(client, app):
    _login_admin(client)
    rv = client.post(
        "/api/v2/campaigns",
        json={
            "name": "Annual Fund 2026",
            "campaign_type": "annual",
            "goal_amount": 50000.0,
            "currency": "USD",
            "status": "active",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "description": "Year-end giving campaign",
        },
    )
    assert rv.status_code == 201
    body = rv.get_json()
    assert "id" in body
    assert "slug" in body
    assert body["slug"] == "annual-fund-2026"


def test_create_campaign_invalid_type_returns_400(client):
    _login_admin(client)
    rv = client.post(
        "/api/v2/campaigns",
        json={"name": "Bad", "campaign_type": "invalid_type"},
    )
    assert rv.status_code == 400


def test_create_campaign_invalid_date_returns_400(client):
    _login_admin(client)
    rv = client.post(
        "/api/v2/campaigns",
        json={"name": "Bad Dates", "start_date": "not-a-date"},
    )
    assert rv.status_code == 400


def test_create_campaign_end_before_start_returns_400(client):
    _login_admin(client)
    rv = client.post(
        "/api/v2/campaigns",
        json={
            "name": "Date Order Error",
            "start_date": "2026-12-01",
            "end_date": "2026-01-01",
        },
    )
    assert rv.status_code == 400


def test_create_duplicate_slug_gets_suffixed(client, app):
    _login_admin(client)
    # Create first campaign
    client.post("/api/v2/campaigns", json={"name": "Unique Slug Test"})
    # Create second with same name — slug should be auto-suffixed
    rv = client.post("/api/v2/campaigns", json={"name": "Unique Slug Test"})
    assert rv.status_code == 201
    body = rv.get_json()
    assert body["slug"] != "unique-slug-test"  # should have a suffix


# ---------------------------------------------------------------------------
# GET DETAIL / STATS
# ---------------------------------------------------------------------------

def test_get_campaign_not_found(client):
    _login_admin(client)
    rv = client.get("/api/v2/campaigns/999999")
    assert rv.status_code == 404


def test_get_campaign_stats(client, app):
    _login_admin(client)
    create_rv = client.post(
        "/api/v2/campaigns",
        json={"name": "Stats Test Campaign", "goal_amount": 10000.0},
    )
    assert create_rv.status_code == 201
    campaign_id = create_rv.get_json()["id"]

    rv = client.get(f"/api/v2/campaigns/{campaign_id}")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["id"] == campaign_id
    assert body["goal_amount"] == 10000.0
    assert body["raised_amount"] == 0.0
    assert body["progress_pct"] == 0.0
    assert body["p2p_page_count"] == 0


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------

def test_update_campaign(client, app):
    _login_admin(client)
    create_rv = client.post(
        "/api/v2/campaigns",
        json={"name": "Update Me Campaign", "goal_amount": 5000.0},
    )
    campaign_id = create_rv.get_json()["id"]

    rv = client.patch(
        f"/api/v2/campaigns/{campaign_id}",
        json={"status": "active", "goal_amount": 7500.0},
    )
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["status"] == "active"


def test_update_campaign_invalid_status_returns_400(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Status Validation Camp"})
    campaign_id = create_rv.get_json()["id"]
    rv = client.patch(f"/api/v2/campaigns/{campaign_id}", json={"status": "bogus"})
    assert rv.status_code == 400


# ---------------------------------------------------------------------------
# CLOSE
# ---------------------------------------------------------------------------

def test_close_campaign(client, app):
    _login_admin(client)
    create_rv = client.post(
        "/api/v2/campaigns",
        json={"name": "Close Me Campaign", "status": "active"},
    )
    campaign_id = create_rv.get_json()["id"]

    rv = client.post(f"/api/v2/campaigns/{campaign_id}/close")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["status"] == "closed"


def test_close_nonexistent_campaign_returns_404(client):
    _login_admin(client)
    rv = client.post("/api/v2/campaigns/999999/close")
    assert rv.status_code == 404


# ---------------------------------------------------------------------------
# STAFF CAN READ BUT NOT CREATE
# ---------------------------------------------------------------------------

def test_staff_can_list_campaigns(client):
    _login_staff(client)
    rv = client.get("/api/v2/campaigns")
    assert rv.status_code == 200


def test_staff_cannot_create_campaign(client):
    _login_staff(client)
    rv = client.post("/api/v2/campaigns", json={"name": "Staff Should Fail"})
    assert rv.status_code in (403, 302)


# ---------------------------------------------------------------------------
# CAMPAIGN BULK EMAIL
# ---------------------------------------------------------------------------

def test_campaign_send_email_requires_subject_and_body(client):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Email Validation Campaign"})
    assert create_rv.status_code == 201
    campaign_id = create_rv.get_json()["id"]

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={"subject": "Only subject"},
    )
    assert rv.status_code == 400


def test_campaign_send_email_all_donors_persists_batch_and_deliveries(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Bulk Send Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        d1 = Donor(organization_id=org_id, name="Campaign Donor A", email="campaign.a@example.org", donor_type="individual")
        d2 = Donor(organization_id=org_id, name="Campaign Donor B", email="campaign.b@example.org", donor_type="individual")
        d3 = Donor(organization_id=org_id, name="No Email Donor", email=None, donor_type="individual")
        db.session.add_all([d1, d2, d3])
        db.session.commit()

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Impact update",
                "body": "Hello {name}, support {campaign_name} today.",
                "audience": {},
            },
        )

    assert rv.status_code == 200
    body = rv.get_json() or {}
    assert body.get("total_recipients") >= 2
    assert body.get("sent") >= 2
    assert send_mock.call_count >= 2

    with app.app_context():
        batch = db.session.scalars(
            db.select(CampaignEmailBatch).where(CampaignEmailBatch.campaign_id == campaign_id).order_by(CampaignEmailBatch.id.desc())
        ).first()
        assert batch is not None
        assert batch.total_recipients >= 2
        assert batch.sent_count >= 2
        deliveries = list(
            db.session.scalars(
                db.select(CampaignEmailDelivery).where(CampaignEmailDelivery.batch_id == batch.id)
            )
        )
        assert len(deliveries) == batch.total_recipients


def test_campaign_send_email_campaign_donors_only_filter(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Campaign Donor Filter"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        d1 = Donor(organization_id=org_id, name="Linked Donor", email="linked@example.org", donor_type="individual")
        d2 = Donor(organization_id=org_id, name="Unlinked Donor", email="unlinked@example.org", donor_type="individual")
        db.session.add_all([d1, d2])
        db.session.flush()
        db.session.add(
            Donation(
                organization_id=org_id,
                campaign_id=campaign_id,
                donor_id=d1.id,
                donor_name=d1.name,
                donor_email=d1.email,
                amount=50.0,
                currency="USD",
                status="received",
            )
        )
        db.session.commit()

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Campaign update",
                "body": "Hi {name}, this is for {campaign_name}.",
                "audience": {"campaign_donors_only": True},
            },
        )

    assert rv.status_code == 200
    body = rv.get_json() or {}
    assert body.get("total_recipients") == 1
    assert send_mock.call_count == 1


def test_campaign_email_analytics_reports_sent_and_failed(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Campaign Analytics"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        d1 = Donor(organization_id=org_id, name="Analytics Donor 1", email="analytics1@example.org", donor_type="individual")
        d2 = Donor(organization_id=org_id, name="Analytics Donor 2", email="analytics2@example.org", donor_type="individual")
        db.session.add_all([d1, d2])
        db.session.flush()
        donor_ids = [int(d1.id), int(d2.id)]
        db.session.commit()

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", side_effect=[True, False]):
        send_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={"subject": "Metrics", "body": "Body text", "audience": {"donor_ids": donor_ids}},
        )
    assert send_rv.status_code == 200

    analytics_rv = client.get(f"/api/v2/campaigns/{campaign_id}/emails/analytics")
    assert analytics_rv.status_code == 200
    payload = analytics_rv.get_json() or {}
    assert payload.get("campaign_id") == campaign_id
    assert payload.get("batch_count", 0) >= 1
    assert payload.get("total_sent", 0) >= 1
    assert payload.get("total_failed", 0) >= 1


def test_viewer_cannot_send_campaign_email(client):
    _login_viewer(client)
    rv = client.post(
        "/api/v2/campaigns/1/emails/send",
        json={"subject": "Nope", "body": "Not allowed"},
    )
    assert rv.status_code in (403, 302)
