"""Tests for campaign V2 API routes."""
from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Campaign, Organization, db


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
