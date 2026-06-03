"""Tests for campaign V2 API routes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
import unittest.mock as mock
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import (
    Campaign,
    CampaignCommunicationPreference,
    CampaignEmailBatch,
    CampaignEmailDelivery,
    CampaignEmailOptOut,
    Donation,
    Donor,
    ExternalCommunicationAuthorization,
    Organization,
    User,
    db,
)


@pytest.fixture(scope="module")
def app():
    class _Cfg(TestingConfig):
        SECRET_KEY = "test-campaign"
        ROLES_REQUIRING_2FA = []

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
    with client.session_transaction() as sess:
        sess["_step_up_verified_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


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


def _human_authorization_payload(
    *,
    reviewer_name: str = "Test Reviewer",
    reviewer_role: str = "Operations Lead",
    ai_assisted: bool = False,
    contains_internal_details: bool = False,
) -> dict:
    return {
        "ai_assisted": ai_assisted,
        "contains_internal_details": contains_internal_details,
        "reviewer_name": reviewer_name,
        "reviewer_role": reviewer_role,
        "warning_acknowledged": True,
        "human_confirmation_text": "I CONFIRM HUMAN REVIEW",
    }


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


def test_campaign_photo_upload_persists_and_returns_media_url(client, app):
    _login_admin(client)
    create_rv = client.post(
        "/api/v2/campaigns",
        json={"name": "Campaign Photo Upload", "goal_amount": 1500.0},
    )
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    upload_rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/photo",
        data={"photo": (BytesIO(b"fake-png-bytes"), "campaign.png")},
        content_type="multipart/form-data",
    )
    assert upload_rv.status_code == 200
    payload = upload_rv.get_json()
    assert payload["photo_url"] == f"/media/campaigns/{campaign_id}/photo"

    list_rv = client.get("/api/v2/campaigns")
    assert list_rv.status_code == 200
    items = list_rv.get_json()
    item = next((row for row in items if int(row["id"]) == campaign_id), None)
    assert item is not None
    assert item["photo_url"] == f"/media/campaigns/{campaign_id}/photo"

    with app.app_context():
        campaign = db.session.get(Campaign, campaign_id)
        assert campaign is not None
        assert campaign.photo_path
        file_path = Path(app.instance_path) / str(campaign.photo_path)
        assert file_path.exists()


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
                "compliance": _human_authorization_payload(),
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
                "compliance": _human_authorization_payload(),
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
            json={
                "subject": "Metrics",
                "body": "Body text",
                "audience": {"donor_ids": donor_ids},
                "compliance": _human_authorization_payload(),
            },
        )
    assert send_rv.status_code == 200

    analytics_rv = client.get(f"/api/v2/campaigns/{campaign_id}/emails/analytics")
    assert analytics_rv.status_code == 200
    payload = analytics_rv.get_json() or {}
    assert payload.get("campaign_id") == campaign_id
    assert payload.get("batch_count", 0) >= 1
    assert payload.get("total_sent", 0) >= 1
    assert payload.get("total_failed", 0) >= 1


def test_campaign_email_analytics_includes_suppression_and_opt_out_metrics(client, app):
    from ngo_homesuite.services.campaign_email_service import _unsub_signature

    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Campaign Analytics Suppression"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Suppression Metrics Donor",
            email="suppression-metrics@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    webhook_rv = client.post(
        "/integrations/email/webhooks/suppression",
        json={"email": "provider-suppressed@example.org", "reason": "complaint"},
    )
    assert webhook_rv.status_code == 200

    ts = int(time.time())
    unsub_email = "suppression-metrics@example.org"
    with app.app_context():
        sig = _unsub_signature(email=unsub_email, donor_id=donor_id, campaign_id=campaign_id, issued_at=ts)

    unsub_rv = client.get(
        "/api/v2/campaigns/email/unsubscribe",
        query_string={
            "email": unsub_email,
            "donor_id": donor_id,
            "campaign_id": campaign_id,
            "ts": ts,
            "sig": sig,
        },
    )
    assert unsub_rv.status_code == 200

    analytics_rv = client.get(f"/api/v2/campaigns/{campaign_id}/emails/analytics")
    assert analytics_rv.status_code == 200
    payload = analytics_rv.get_json() or {}

    assert int(payload.get("opt_out_count") or 0) >= 1
    assert int(payload.get("campaign_opt_out_count") or 0) >= 1
    assert int(payload.get("suppression_count") or 0) >= 1
    breakdown = payload.get("suppression_reason_breakdown") or {}
    assert int(breakdown.get("complaint") or 0) >= 1


def test_viewer_cannot_send_campaign_email(client):
    _login_viewer(client)
    rv = client.post(
        "/api/v2/campaigns/1/emails/send",
        json={"subject": "Nope", "body": "Not allowed"},
    )
    assert rv.status_code in (403, 302)


def test_staff_without_external_comms_permission_cannot_send_campaign_email(client, app):
    with app.app_context():
        staff_user = User.query.filter_by(username="staff").first()
        if staff_user is not None:
            staff_user.can_authorize_external_comms = False
            db.session.commit()

    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Staff Unauthorized Send Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    client.post("/auth/logout", follow_redirects=False)
    _login_staff(client)

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "Attempted unauthorized send",
            "body": "Hello {name}, update from {campaign_name}.",
            "audience": {},
            "compliance": _human_authorization_payload(),
        },
    )
    assert rv.status_code == 403
    payload = rv.get_json() or {}
    assert payload.get("required_permission") == "can_authorize_external_comms"


def test_staff_with_admin_granted_external_comms_permission_can_send(client, app):
    org_id = _admin_org_id(app)
    with app.app_context():
        staff_user = User.query.filter_by(username="staff").first()
        assert staff_user is not None
        staff_user.can_authorize_external_comms = True

        donor = Donor(
            organization_id=org_id,
            name="Staff Permitted Donor",
            email="staff-permitted@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Staff Permitted Send Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    client.post("/auth/logout", follow_redirects=False)
    _login_staff(client)

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Authorized by admin permission",
                "body": "Hello {name}, update from {campaign_name}.",
                "audience": {"donor_ids": [donor_id]},
                "compliance": _human_authorization_payload(reviewer_name="Staff Reviewer", reviewer_role="Staff"),
            },
        )

    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("total_recipients") == 1
    assert send_mock.call_count == 1


def test_campaign_email_preview_returns_quality_hints_and_samples(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Preview Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        d1 = Donor(organization_id=org_id, name="Preview Donor", email="preview@example.org", donor_type="individual")
        db.session.add(d1)
        db.session.flush()
        preview_donor_id = int(d1.id)
        db.session.commit()

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/preview",
        json={
            "subject": "Hi",
            "body": "Support our campaign.",
            "audience": {"donor_ids": [preview_donor_id]},
        },
    )
    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("total_recipients", 0) >= 1
    assert isinstance(payload.get("quality_hints"), list)
    assert isinstance(payload.get("sample_preview"), list)
    assert isinstance(payload.get("recipient_breakdown"), dict)
    breakdown = payload.get("recipient_breakdown") or {}
    assert isinstance(breakdown.get("by_donor_type"), dict)
    assert isinstance(breakdown.get("by_total_giving_band"), dict)
    assert isinstance(breakdown.get("by_gift_count_band"), dict)
    assert isinstance(breakdown.get("by_recency_band"), dict)
    assert payload["sample_preview"][0]["recipient_email"] == "preview@example.org"


def test_campaign_email_preview_reflects_advanced_filtering_and_breakdowns(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Preview Filtered Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    with app.app_context():
        d1 = Donor(organization_id=org_id, name="Preview Match", email="preview-match@example.org", donor_type="individual")
        d2 = Donor(organization_id=org_id, name="Preview Excluded", email="preview-excluded@example.org", donor_type="individual")
        db.session.add_all([d1, d2])
        db.session.flush()

        db.session.add_all(
            [
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d1.id,
                    donor_name=d1.name,
                    donor_email=d1.email,
                    amount=300.0,
                    currency="USD",
                    status="received",
                    donation_date=now_naive - timedelta(days=40),
                ),
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d1.id,
                    donor_name=d1.name,
                    donor_email=d1.email,
                    amount=80.0,
                    currency="USD",
                    status="received",
                    donation_date=now_naive - timedelta(days=15),
                ),
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d2.id,
                    donor_name=d2.name,
                    donor_email=d2.email,
                    amount=950.0,
                    currency="USD",
                    status="received",
                    donation_date=now_naive - timedelta(days=20),
                ),
            ]
        )
        db.session.commit()

    audience = {
        "min_total_given": 300.0,
        "max_total_given": 500.0,
        "min_gift_count": 2,
        "max_gift_count": 3,
        "gifted_between_days_min": 1,
        "gifted_between_days_max": 90,
    }
    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/preview",
        json={
            "subject": "Filtered preview",
            "body": "Hi {name}, previewing {campaign_name}.",
            "audience": audience,
        },
    )
    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert int(payload.get("total_recipients") or 0) == 1
    assert (payload.get("sample_preview") or [])[0]["recipient_email"] == "preview-match@example.org"

    applied = payload.get("audience_applied") or {}
    assert int(applied.get("min_gift_count") or 0) == 2
    assert int(applied.get("max_gift_count") or 0) == 3

    breakdown = payload.get("recipient_breakdown") or {}
    total_bands = breakdown.get("by_total_giving_band") or {}
    gift_bands = breakdown.get("by_gift_count_band") or {}
    recency_bands = breakdown.get("by_recency_band") or {}
    assert int(total_bands.get("100-499") or 0) == 1
    assert int(gift_bands.get("2-5") or 0) == 1
    assert int(recency_bands.get("0-30d") or 0) == 1


def test_campaign_email_deliverability_endpoint_returns_policy_checks(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Deliverability Check Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Deliverability Donor",
            email="deliverability@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/deliverability",
        json={
            "subject": "Deliverability preview",
            "body": "Hello {name}, support {campaign_name}.",
            "audience": {"donor_ids": [donor_id]},
        },
    )
    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("campaign_id") == campaign_id
    assert isinstance(payload.get("checks"), list)
    assert int(payload.get("total_recipients") or 0) == 1


def test_campaign_send_email_blocks_when_sender_domain_violates_policy(client, app, monkeypatch):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Sender Domain Policy Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Sender Policy Donor",
            email="sender-policy@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

        monkeypatch.setitem(app.config, "CAMPAIGN_EMAIL_ALLOWED_SENDER_DOMAINS", "approved.org")
        monkeypatch.setitem(app.config, "DEFAULT_MAIL_SENDER", "mailer@unapproved.org")
        monkeypatch.setitem(app.config, "CAMPAIGN_EMAIL_ENFORCE_PRECHECKS", True)

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "Policy violation check",
            "body": "Hello {name}, support {campaign_name}.",
            "audience": {"donor_ids": [donor_id]},
            "compliance": _human_authorization_payload(),
        },
    )
    assert rv.status_code == 400
    payload = rv.get_json() or {}
    assert "deliverability precheck failed" in str(payload.get("error") or "")


def test_campaign_ai_draft_uses_fallback_when_ai_unavailable(client, app):
    _login_staff(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "AI Draft Campaign"})
    # staff cannot create; create as admin then log back as staff
    if create_rv.status_code != 201:
        client.post("/auth/logout", follow_redirects=False)
        _login_admin(client)
        create_rv = client.post("/api/v2/campaigns", json={"name": "AI Draft Campaign"})
        assert create_rv.status_code == 201
        client.post("/auth/logout", follow_redirects=False)
        _login_staff(client)
    campaign_id = int(create_rv.get_json()["id"])

    with mock.patch("ngo_homesuite.ai.apex_client.ApexClient.query", side_effect=Exception("offline")):
        rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/ai-draft",
            json={"objective": "re-engage lapsed donors", "tone": "optimistic"},
        )
    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("generated_by") in ("fallback", "ai")
    assert "subject" in payload and payload["subject"]
    assert "body" in payload and payload["body"]


def test_campaign_send_email_respects_min_total_and_top_n_filters(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Filtered Audience Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        d1 = Donor(organization_id=org_id, name="High Donor", email="high@example.org", donor_type="individual")
        d2 = Donor(organization_id=org_id, name="Low Donor", email="low@example.org", donor_type="individual")
        d3 = Donor(organization_id=org_id, name="Mid Donor", email="mid@example.org", donor_type="individual")
        db.session.add_all([d1, d2, d3])
        db.session.flush()

        db.session.add_all(
            [
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d1.id,
                    donor_name=d1.name,
                    donor_email=d1.email,
                    amount=500.0,
                    currency="USD",
                    status="received",
                ),
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d2.id,
                    donor_name=d2.name,
                    donor_email=d2.email,
                    amount=20.0,
                    currency="USD",
                    status="received",
                ),
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d3.id,
                    donor_name=d3.name,
                    donor_email=d3.email,
                    amount=120.0,
                    currency="USD",
                    status="received",
                ),
            ]
        )
        db.session.commit()

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Filtered blast",
                "body": "Hi {name}, support {campaign_name}.",
                "audience": {"min_total_given": 100.0, "top_n_by_total_given": 1},
                "compliance": _human_authorization_payload(),
            },
        )

    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("total_recipients") == 1
    assert send_mock.call_count == 1


def test_campaign_send_email_respects_total_gift_count_and_recency_band_filters(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Advanced Audience Filters Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    with app.app_context():
        d1 = Donor(organization_id=org_id, name="Balanced Donor", email="balanced@example.org", donor_type="individual")
        d2 = Donor(organization_id=org_id, name="High Total Donor", email="high-total@example.org", donor_type="individual")
        d3 = Donor(organization_id=org_id, name="Old Gift Donor", email="old-gift@example.org", donor_type="individual")
        db.session.add_all([d1, d2, d3])
        db.session.flush()
        target_donor_ids = [int(d1.id), int(d2.id), int(d3.id)]

        # d1: within all bounds (2 gifts, total 250, last gift 20 days ago)
        db.session.add_all(
            [
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d1.id,
                    donor_name=d1.name,
                    donor_email=d1.email,
                    amount=150.0,
                    currency="USD",
                    status="received",
                    donation_date=now_naive - timedelta(days=45),
                ),
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d1.id,
                    donor_name=d1.name,
                    donor_email=d1.email,
                    amount=100.0,
                    currency="USD",
                    status="received",
                    donation_date=now_naive - timedelta(days=20),
                ),
                # d2: excluded by max_total_given
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d2.id,
                    donor_name=d2.name,
                    donor_email=d2.email,
                    amount=900.0,
                    currency="USD",
                    status="received",
                    donation_date=now_naive - timedelta(days=10),
                ),
                # d3: excluded by recency band (too old)
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d3.id,
                    donor_name=d3.name,
                    donor_email=d3.email,
                    amount=220.0,
                    currency="USD",
                    status="received",
                    donation_date=now_naive - timedelta(days=220),
                ),
            ]
        )
        db.session.commit()

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Advanced filter blast",
                "body": "Hi {name}, support {campaign_name}.",
                "audience": {
                    "donor_ids": target_donor_ids,
                    "min_total_given": 200.0,
                    "max_total_given": 400.0,
                    "min_gift_count": 2,
                    "max_gift_count": 3,
                    "gifted_between_days_min": 10,
                    "gifted_between_days_max": 90,
                },
                "compliance": _human_authorization_payload(),
            },
        )

    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("total_recipients") == 1
    assert send_mock.call_count == 1
    sent_emails = [str(call.kwargs.get("to") or "").strip().lower() for call in send_mock.call_args_list]
    assert sent_emails == ["balanced@example.org"]


def test_campaign_send_email_rejects_invalid_recency_band_filter_range(client):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Invalid Recency Band Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "Invalid range",
            "body": "Hi {name}.",
            "audience": {
                "gifted_between_days_min": 60,
                "gifted_between_days_max": 10,
            },
            "compliance": _human_authorization_payload(),
        },
    )

    assert rv.status_code == 400
    payload = rv.get_json() or {}
    assert "gifted_between_days_max" in str(payload.get("error") or "")


def test_campaign_send_email_accepts_smart_group_audience(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Smart Group Audience Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        d1 = Donor(organization_id=org_id, name="Segment High", email="segment-high@example.org", donor_type="individual")
        d2 = Donor(organization_id=org_id, name="Segment Low", email="segment-low@example.org", donor_type="individual")
        db.session.add_all([d1, d2])
        db.session.flush()

        db.session.add_all(
            [
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d1.id,
                    donor_name=d1.name,
                    donor_email=d1.email,
                    amount=500.0,
                    currency="USD",
                    status="received",
                ),
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d2.id,
                    donor_name=d2.name,
                    donor_email=d2.email,
                    amount=25.0,
                    currency="USD",
                    status="received",
                ),
            ]
        )
        db.session.commit()

    sg_rv = client.post(
        "/api/v2/smart-groups",
        json={
            "name": f"High Value Segment {campaign_id}",
            "rules": [{"field": "total_giving", "op": "gte", "value": 300}],
            "description": "High-value donors for campaign send",
        },
    )
    assert sg_rv.status_code == 201
    smart_group_id = int((sg_rv.get_json() or {}).get("id"))

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        send_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Segmented outreach",
                "body": "Hello {name}, this was sent to a saved segment for {campaign_name}.",
                "audience": {"smart_group_id": smart_group_id},
                "compliance": _human_authorization_payload(),
            },
        )

    assert send_rv.status_code == 200
    payload = send_rv.get_json() or {}
    assert int(payload.get("total_recipients") or 0) >= 1
    sent_emails = [str(call.kwargs.get("to") or "").strip().lower() for call in send_mock.call_args_list]
    assert "segment-high@example.org" in sent_emails
    assert "segment-low@example.org" not in sent_emails


def test_campaign_send_email_rejects_invalid_smart_group_audience(client):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Invalid Segment Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "Bad segment",
            "body": "Hello {name}.",
            "audience": {"smart_group_id": 999999},
            "compliance": _human_authorization_payload(),
        },
    )
    assert rv.status_code == 400
    payload = rv.get_json() or {}
    assert "smart_group_id" in str(payload.get("error") or "")


def test_campaign_send_email_accepts_inline_smart_group_rules_audience(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Inline Rules Segment Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        d1 = Donor(organization_id=org_id, name="Rules High", email="rules-high@example.org", donor_type="individual")
        d2 = Donor(organization_id=org_id, name="Rules Low", email="rules-low@example.org", donor_type="individual")
        db.session.add_all([d1, d2])
        db.session.flush()

        db.session.add_all(
            [
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d1.id,
                    donor_name=d1.name,
                    donor_email=d1.email,
                    amount=600.0,
                    currency="USD",
                    status="received",
                ),
                Donation(
                    organization_id=org_id,
                    campaign_id=campaign_id,
                    donor_id=d2.id,
                    donor_name=d2.name,
                    donor_email=d2.email,
                    amount=50.0,
                    currency="USD",
                    status="received",
                ),
            ]
        )
        db.session.commit()

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        send_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Inline rules outreach",
                "body": "Hello {name}, this came from inline rules for {campaign_name}.",
                "audience": {
                    "smart_group_rules": [
                        {"field": "total_giving", "op": "gte", "value": 300},
                    ]
                },
                "compliance": _human_authorization_payload(),
            },
        )

    assert send_rv.status_code == 200
    payload = send_rv.get_json() or {}
    assert int(payload.get("total_recipients") or 0) >= 1
    sent_emails = [str(call.kwargs.get("to") or "").strip().lower() for call in send_mock.call_args_list]
    assert "rules-high@example.org" in sent_emails
    assert "rules-low@example.org" not in sent_emails


def test_campaign_send_email_rejects_invalid_inline_smart_group_rules_payload(client):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Invalid Inline Rules Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "Bad inline rules",
            "body": "Hello {name}.",
            "audience": {"smart_group_rules": "not-a-list"},
            "compliance": _human_authorization_payload(),
        },
    )
    assert rv.status_code == 400
    payload = rv.get_json() or {}
    assert "smart_group_rules" in str(payload.get("error") or "")


def test_campaign_email_segments_list_endpoint_returns_saved_groups(client):
    _login_admin(client)
    sg_rv = client.post(
        "/api/v2/smart-groups",
        json={
            "name": f"Email Segment List {int(time.time())}",
            "rules": [{"field": "gift_count", "op": "gte", "value": 1}],
            "description": "Segment list endpoint coverage",
        },
    )
    assert sg_rv.status_code == 201
    segment_id = int((sg_rv.get_json() or {}).get("id"))

    list_rv = client.get("/api/v2/campaigns/email/segments")
    assert list_rv.status_code == 200
    payload = list_rv.get_json() or []
    assert isinstance(payload, list)
    ids = {int(item.get("id")) for item in payload if str(item.get("id") or "").strip().isdigit()}
    assert segment_id in ids


def test_campaign_email_segment_preview_endpoint_returns_members(client, app):
    _login_admin(client)
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Segment Preview Donor",
            email="segment-preview@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()
        db.session.add(
            Donation(
                organization_id=org_id,
                donor_id=donor.id,
                donor_name=donor.name,
                donor_email=donor.email,
                amount=100.0,
                currency="USD",
                status="received",
            )
        )
        db.session.commit()

    sg_rv = client.post(
        "/api/v2/smart-groups",
        json={
            "name": f"Email Segment Preview {int(time.time())}",
            "rules": [{"field": "gift_count", "op": "gte", "value": 1}],
            "description": "Segment preview endpoint coverage",
        },
    )
    assert sg_rv.status_code == 201
    segment_id = int((sg_rv.get_json() or {}).get("id"))

    preview_rv = client.get(f"/api/v2/campaigns/email/segments/{segment_id}/preview", query_string={"limit": 5})
    assert preview_rv.status_code == 200
    payload = preview_rv.get_json() or {}
    assert int(payload.get("segment_id") or 0) == segment_id
    assert int(payload.get("count") or 0) >= 1
    members = payload.get("members") or []
    assert isinstance(members, list)


def test_campaign_email_segment_quick_create_endpoint_returns_preview(client, app):
    _login_admin(client)
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Quick Segment Donor",
            email="quick-segment@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()
        db.session.add(
            Donation(
                organization_id=org_id,
                donor_id=donor.id,
                donor_name=donor.name,
                donor_email=donor.email,
                amount=250.0,
                currency="USD",
                status="received",
            )
        )
        db.session.commit()

    rv = client.post(
        "/api/v2/campaigns/email/segments",
        json={
            "name": f"Quick Segment {int(time.time())}",
            "description": "Created from campaign composer flow",
            "rules": [{"field": "gift_count", "op": "gte", "value": 1}],
            "include_preview": True,
            "preview_limit": 10,
        },
    )
    assert rv.status_code == 201
    payload = rv.get_json() or {}
    assert int(payload.get("id") or 0) > 0
    assert int(payload.get("count") or 0) >= 1
    members = payload.get("members") or []
    assert isinstance(members, list)


def test_campaign_email_segment_quick_create_rejects_invalid_preview_limit(client):
    _login_admin(client)
    rv = client.post(
        "/api/v2/campaigns/email/segments",
        json={
            "name": "Invalid preview limit segment",
            "rules": [{"field": "gift_count", "op": "gte", "value": 1}],
            "include_preview": True,
            "preview_limit": "abc",
        },
    )
    assert rv.status_code == 400
    payload = rv.get_json() or {}
    assert "preview_limit" in str(payload.get("error") or "")


def test_campaign_email_segment_quick_create_rejects_duplicate_name(client):
    _login_admin(client)
    segment_name = f"Duplicate Segment Name {int(time.time())}"

    first_rv = client.post(
        "/api/v2/campaigns/email/segments",
        json={
            "name": segment_name,
            "rules": [{"field": "gift_count", "op": "gte", "value": 1}],
        },
    )
    assert first_rv.status_code == 201

    second_rv = client.post(
        "/api/v2/campaigns/email/segments",
        json={
            "name": segment_name,
            "rules": [{"field": "gift_count", "op": "gte", "value": 1}],
        },
    )
    assert second_rv.status_code == 409
    payload = second_rv.get_json() or {}
    assert "already exists" in str(payload.get("error") or "")


def test_campaign_email_segment_update_endpoint_renames_segment(client):
    _login_admin(client)
    segment_name = f"Segment Rename Source {int(time.time())}"

    create_rv = client.post(
        "/api/v2/campaigns/email/segments",
        json={
            "name": segment_name,
            "rules": [{"field": "gift_count", "op": "gte", "value": 1}],
        },
    )
    assert create_rv.status_code == 201
    segment_id = int((create_rv.get_json() or {}).get("id"))

    rename_rv = client.patch(
        f"/api/v2/campaigns/email/segments/{segment_id}",
        json={"name": f"Segment Rename Target {int(time.time())}"},
    )
    assert rename_rv.status_code == 200
    payload = rename_rv.get_json() or {}
    assert int(payload.get("id") or 0) == segment_id
    assert "Segment Rename Target" in str(payload.get("name") or "")


def test_campaign_email_segment_delete_endpoint_removes_segment(client):
    _login_admin(client)
    create_rv = client.post(
        "/api/v2/campaigns/email/segments",
        json={
            "name": f"Segment Delete {int(time.time())}",
            "rules": [{"field": "gift_count", "op": "gte", "value": 1}],
        },
    )
    assert create_rv.status_code == 201
    segment_id = int((create_rv.get_json() or {}).get("id"))

    delete_rv = client.delete(f"/api/v2/campaigns/email/segments/{segment_id}")
    assert delete_rv.status_code == 200
    body = delete_rv.get_json() or {}
    assert body.get("deleted") is True

    preview_rv = client.get(f"/api/v2/campaigns/email/segments/{segment_id}/preview")
    assert preview_rv.status_code == 404


def test_campaign_email_attribution_endpoint_returns_influenced_metrics(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Attribution Metrics Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Attribution Donor",
            email="attribution@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        sent_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        batch = CampaignEmailBatch(
            organization_id=org_id,
            campaign_id=campaign_id,
            created_by_user_id=1,
            subject="Attribution touch",
            body="Hello {name}",
            status="sent",
            total_recipients=1,
            sent_count=1,
            failed_count=0,
            sent_at=sent_at,
        )
        db.session.add(batch)
        db.session.flush()

        db.session.add(
            CampaignEmailDelivery(
                batch_id=batch.id,
                organization_id=org_id,
                campaign_id=campaign_id,
                donor_id=donor.id,
                recipient_email=donor.email,
                delivery_status="sent",
                sent_at=sent_at,
            )
        )
        db.session.add(
            Donation(
                organization_id=org_id,
                campaign_id=campaign_id,
                donor_id=donor.id,
                donor_name=donor.name,
                donor_email=donor.email,
                amount=180.0,
                currency="USD",
                status="received",
                donation_date=sent_at + timedelta(days=1),
            )
        )
        db.session.commit()

    rv = client.get(f"/api/v2/campaigns/{campaign_id}/emails/attribution", query_string={"window_days": 30})
    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert int(payload.get("influenced_donations") or 0) >= 1
    assert float(payload.get("influenced_revenue") or 0.0) >= 180.0
    assert int(payload.get("influenced_donor_count") or 0) >= 1


def test_campaign_email_automation_templates_and_instantiate_route(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Template Automation Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Template Recipient",
            email="template-sequence@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    templates_rv = client.get(f"/api/v2/campaigns/{campaign_id}/emails/automation/templates")
    assert templates_rv.status_code == 200
    templates = templates_rv.get_json() or []
    assert isinstance(templates, list)
    assert any(str(item.get("key") or "") == "welcome_nurture" for item in templates)

    instantiate_rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/automation/templates/welcome_nurture/instantiate",
        json={
            "audience": {"donor_ids": [donor_id]},
            "compliance": _human_authorization_payload(),
        },
    )
    assert instantiate_rv.status_code == 200
    payload = instantiate_rv.get_json() or {}
    assert payload.get("template_key") == "welcome_nurture"
    assert int(payload.get("step_count") or 0) >= 1
    assert isinstance(payload.get("batches"), list)


def test_campaign_send_email_ai_assisted_requires_human_in_loop_confirmation(client):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "AI HITL Guardrail Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "AI-assisted outbound",
            "body": "Hello {name}, update from {campaign_name}",
            "audience": {},
            "compliance": {
                "ai_assisted": True,
                "contains_internal_details": False,
                "reviewer_name": "",
                "warning_acknowledged": False,
                "human_confirmation_text": "",
            },
        },
    )
    assert rv.status_code == 400
    payload = rv.get_json() or {}
    assert payload.get("human_in_the_loop_required") is True
    assert "Human reviewer name is required" in (payload.get("error") or "")


def test_campaign_send_email_requires_human_authorization_for_non_ai_message(client):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Universal HITL Requirement"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "Standard external outreach",
            "body": "Hello {name}, a regular update from {campaign_name}.",
            "audience": {},
        },
    )
    assert rv.status_code == 400
    payload = rv.get_json() or {}
    assert payload.get("human_in_the_loop_required") is True
    assert "outbound external communication" in (payload.get("warning") or "")


def test_campaign_send_email_rate_limited_returns_429(client, app, monkeypatch):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Rate Limited Send Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    monkeypatch.setattr("ngo_homesuite.web.v2_routes._campaign_send_limited", lambda _campaign_id: (True, 7))

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "Rate limited",
            "body": "Hello {name}",
            "audience": {},
            "compliance": _human_authorization_payload(),
        },
    )

    assert rv.status_code == 429
    payload = rv.get_json() or {}
    assert "rate limit" in str(payload.get("error") or "").lower()
    assert int(payload.get("retry_after_sec") or 0) == 7
    assert rv.headers.get("Retry-After") == "7"


def test_campaign_send_email_internal_details_allows_confirmed_human_in_loop(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Internal Detail HITL Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(organization_id=org_id, name="HITL Donor", email="hitl@example.org", donor_type="individual")
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Internal campaign performance update",
                "body": "Hello {name}, this includes internal planning context for {campaign_name}.",
                "audience": {"donor_ids": [donor_id]},
                "compliance": {
                    "ai_assisted": False,
                    "contains_internal_details": True,
                    "reviewer_name": "Alex Grant",
                    "reviewer_role": "Fundraising Director",
                    "warning_acknowledged": True,
                    "human_confirmation_text": "I CONFIRM HUMAN REVIEW",
                },
            },
        )

    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("total_recipients") == 1
    assert send_mock.call_count == 1
    assert int(payload.get("authorization_audit_id") or 0) > 0
    assert payload.get("authorized_at")

    with app.app_context():
        batch = db.session.scalars(
            db.select(CampaignEmailBatch).where(CampaignEmailBatch.campaign_id == campaign_id).order_by(CampaignEmailBatch.id.desc())
        ).first()
        assert batch is not None
        hitl = (batch.audience_json or {}).get("_human_in_the_loop") or {}
        assert hitl.get("required") is True
        assert hitl.get("contains_internal_details") is True
        assert hitl.get("reviewer_name") == "Alex Grant"

        auth = db.session.scalars(
            db.select(ExternalCommunicationAuthorization).where(
                ExternalCommunicationAuthorization.id == int(payload.get("authorization_audit_id")),
            )
        ).first()
        assert auth is not None
        assert int(auth.user_id) > 0
        assert auth.username
        assert auth.authorized_at is not None
        assert int(auth.batch_id or 0) == int(batch.id)


def test_campaign_send_email_can_be_scheduled_without_immediate_delivery(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Scheduled Delivery Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(organization_id=org_id, name="Scheduled Donor", email="scheduled@example.org", donor_type="individual")
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    scheduled_at = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)).isoformat()

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Scheduled campaign send",
                "body": "Hi {name}, this is a scheduled update for {campaign_name}.",
                "audience": {"donor_ids": [donor_id]},
                "scheduled_at": scheduled_at,
                "compliance": _human_authorization_payload(),
            },
        )

    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("scheduled") is True
    assert payload.get("status") == "scheduled"
    assert send_mock.call_count == 0

    with app.app_context():
        batch = db.session.scalars(
            db.select(CampaignEmailBatch).where(CampaignEmailBatch.id == int(payload.get("batch_id"))).limit(1)
        ).first()
        assert batch is not None
        assert batch.status == "scheduled"
        assert batch.scheduled_at is not None
        assert int(batch.total_recipients) == 1


def test_campaign_email_automation_sequence_schedules_multiple_batches(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Automation Sequence Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Automation Donor",
            email="automation-donor@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/automation/sequence",
        json={
            "subject": "Automation touchpoint",
            "body": "Hello {name}, this is an automation sequence for {campaign_name}.",
            "audience": {"donor_ids": [donor_id]},
            "step_count": 3,
            "cadence_days": 5,
            "compliance": _human_authorization_payload(),
        },
    )
    assert rv.status_code == 200
    payload = rv.get_json() or {}
    assert payload.get("automation") == "drip_sequence"
    assert int(payload.get("step_count") or 0) == 3
    batches = payload.get("batches") or []
    assert len(batches) == 3

    with app.app_context():
        rows = list(
            db.session.scalars(
                db.select(CampaignEmailBatch).where(CampaignEmailBatch.campaign_id == campaign_id)
            )
        )
        assert len(rows) >= 3
        statuses = {str(row.status) for row in rows}
        assert "scheduled" in statuses


def test_campaign_email_automation_sequence_validates_step_count(client):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Automation Validation Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])

    rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/automation/sequence",
        json={
            "subject": "Validation",
            "body": "Hello {name}",
            "audience": {},
            "step_count": 0,
            "cadence_days": 3,
            "compliance": _human_authorization_payload(),
        },
    )
    assert rv.status_code == 400
    payload = rv.get_json() or {}
    assert "step_count" in str(payload.get("error") or "")


def test_campaign_unsubscribe_creates_opt_out_and_suppresses_future_send(client, app):
    from ngo_homesuite.services.campaign_email_service import _unsub_signature

    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Unsubscribe Suppression Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(organization_id=org_id, name="Opt Out Donor", email="optout-campaign@example.org", donor_type="individual")
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    ts = int(time.time())
    email = "optout-campaign@example.org"
    with app.app_context():
        sig = _unsub_signature(email=email, donor_id=donor_id, campaign_id=campaign_id, issued_at=ts)

    rv = client.get(
        "/api/v2/campaigns/email/unsubscribe",
        query_string={
            "email": email,
            "donor_id": donor_id,
            "campaign_id": campaign_id,
            "ts": ts,
            "sig": sig,
        },
    )
    assert rv.status_code == 200

    with app.app_context():
        opt_out = db.session.scalars(
            db.select(CampaignEmailOptOut).where(
                CampaignEmailOptOut.organization_id == org_id,
                CampaignEmailOptOut.email == email,
            ).limit(1)
        ).first()
        assert opt_out is not None
        preference = db.session.scalars(
            db.select(CampaignCommunicationPreference).where(
                CampaignCommunicationPreference.organization_id == org_id,
                CampaignCommunicationPreference.email == email,
            ).limit(1)
        ).first()
        assert preference is not None
        assert bool(preference.campaign_opt_in) is False

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        send_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Post-unsubscribe message",
                "body": "Hello {name}, this should be suppressed for opted-out recipients.",
                "audience": {"donor_ids": [donor_id]},
                "compliance": _human_authorization_payload(),
            },
        )

    assert send_rv.status_code == 200
    send_payload = send_rv.get_json() or {}
    assert int(send_payload.get("total_recipients") or 0) == 0
    assert send_mock.call_count == 0


def test_campaign_send_skips_addresses_marked_by_suppression_webhook(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Webhook Suppression Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Suppressed Donor",
            email="suppressed-campaign@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    suppress_rv = client.post(
        "/integrations/email/webhooks/suppression",
        json={"email": "suppressed-campaign@example.org", "reason": "complaint"},
    )
    assert suppress_rv.status_code == 200

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        send_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Suppression verification",
                "body": "Hello {name}, this should never send because of provider suppression.",
                "audience": {"donor_ids": [donor_id]},
                "compliance": _human_authorization_payload(),
            },
        )

    assert send_rv.status_code == 200
    payload = send_rv.get_json() or {}
    assert int(payload.get("total_recipients") or 0) == 0
    assert send_mock.call_count == 0


def test_campaign_public_preference_center_link_reads_and_updates_preferences(client, app):
    from ngo_homesuite.services.campaign_email_service import _preference_signature

    _login_admin(client)
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Preference Center Donor",
            email="preference-center@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    ts = int(time.time())
    email = "preference-center@example.org"
    with app.app_context():
        sig = _preference_signature(email=email, organization_id=org_id, donor_id=donor_id, issued_at=ts)

    get_rv = client.get(
        "/api/v2/campaigns/email/preferences",
        query_string={
            "email": email,
            "organization_id": org_id,
            "donor_id": donor_id,
            "ts": ts,
            "sig": sig,
        },
    )
    assert get_rv.status_code == 200
    initial_payload = get_rv.get_json() or {}
    assert bool(initial_payload.get("campaign_opt_in")) is True
    assert str(initial_payload.get("digest_frequency") or "") == "weekly"

    patch_rv = client.patch(
        "/api/v2/campaigns/email/preferences",
        json={
            "email": email,
            "organization_id": org_id,
            "donor_id": donor_id,
            "ts": ts,
            "sig": sig,
            "newsletter_opt_in": False,
            "campaign_opt_in": True,
            "digest_frequency": "monthly",
        },
    )
    assert patch_rv.status_code == 200
    payload = patch_rv.get_json() or {}
    assert bool(payload.get("newsletter_opt_in")) is False
    assert bool(payload.get("campaign_opt_in")) is True
    assert str(payload.get("digest_frequency") or "") == "monthly"


def test_campaign_send_respects_channel_specific_preferences(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Channel Preference Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Channel Preference Donor",
            email="channel-pref@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)
        pref = CampaignCommunicationPreference(
            organization_id=org_id,
            donor_id=donor_id,
            email="channel-pref@example.org",
            newsletter_opt_in=False,
            campaign_opt_in=True,
            digest_frequency="weekly",
            source="test",
        )
        db.session.add(pref)
        db.session.commit()

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
        newsletter_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Newsletter update",
                "body": "Hello {name}, newsletter content for {campaign_name}.",
                "audience": {"donor_ids": [donor_id], "channel": "newsletter"},
                "compliance": _human_authorization_payload(),
            },
        )
        assert newsletter_rv.status_code == 200
        newsletter_payload = newsletter_rv.get_json() or {}
        assert int(newsletter_payload.get("total_recipients") or 0) == 0

        campaign_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Campaign update",
                "body": "Hello {name}, campaign content for {campaign_name}.",
                "audience": {"donor_ids": [donor_id], "channel": "campaign"},
                "compliance": _human_authorization_payload(),
            },
        )
        assert campaign_rv.status_code == 200
        campaign_payload = campaign_rv.get_json() or {}
        assert int(campaign_payload.get("total_recipients") or 0) == 1
        assert send_mock.call_count == 1


def test_process_scheduled_campaign_batches_dispatches_due_batch(client, app):
    from ngo_homesuite.services.campaign_email_service import process_scheduled_campaign_email_batches

    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Scheduled Processor Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(organization_id=org_id, name="Due Batch Donor", email="duebatch@example.org", donor_type="individual")
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    scheduled_at = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=20)).isoformat()
    queue_rv = client.post(
        f"/api/v2/campaigns/{campaign_id}/emails/send",
        json={
            "subject": "Scheduled dispatch",
            "body": "Hello {name}, this should be sent by the due-batch processor.",
            "audience": {"donor_ids": [donor_id]},
            "scheduled_at": scheduled_at,
            "compliance": _human_authorization_payload(),
        },
    )
    assert queue_rv.status_code == 200
    queue_payload = queue_rv.get_json() or {}
    batch_id = int(queue_payload.get("batch_id"))

    with app.app_context():
        with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True) as send_mock:
            result = process_scheduled_campaign_email_batches(
                limit=10,
                now=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30),
            )
        assert int(result.get("processed_batches") or 0) >= 1
        assert int(result.get("emails_sent") or 0) >= 1
        assert send_mock.call_count >= 1

        batch = db.session.get(CampaignEmailBatch, batch_id)
        assert batch is not None
        assert batch.status in {"sent", "partial_failed"}
        assert int(batch.sent_count or 0) >= 1


def test_campaign_email_queue_overview_and_retry_failed_batch(client, app):
    _login_admin(client)
    create_rv = client.post("/api/v2/campaigns", json={"name": "Queue and Retry Campaign"})
    assert create_rv.status_code == 201
    campaign_id = int(create_rv.get_json()["id"])
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Queue Retry Donor",
            email="queue-retry@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = int(donor.id)

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=False):
        send_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/send",
            json={
                "subject": "Queue retry test",
                "body": "Hello {name}, this send is expected to fail first.",
                "audience": {"donor_ids": [donor_id]},
                "compliance": _human_authorization_payload(),
            },
        )
    assert send_rv.status_code == 200
    failed_batch_id = int((send_rv.get_json() or {}).get("batch_id") or 0)
    assert failed_batch_id > 0

    queue_rv = client.get(f"/api/v2/campaigns/{campaign_id}/emails/queue")
    assert queue_rv.status_code == 200
    queue_payload = queue_rv.get_json() or {}
    assert isinstance(queue_payload.get("status_breakdown"), dict)
    assert any(int(item.get("id") or 0) == failed_batch_id for item in queue_payload.get("recent_batches") or [])

    with mock.patch("ngo_homesuite.services.campaign_email_service.send_email", return_value=True):
        retry_rv = client.post(
            f"/api/v2/campaigns/{campaign_id}/emails/batches/{failed_batch_id}/retry-failed",
            json={"compliance": _human_authorization_payload()},
        )
    assert retry_rv.status_code == 200
    retry_payload = retry_rv.get_json() or {}
    assert int(retry_payload.get("retry_of_batch_id") or 0) == failed_batch_id
    assert int(retry_payload.get("retried_failed_recipients") or 0) == 1
    assert int(retry_payload.get("batch_id") or 0) != failed_batch_id
