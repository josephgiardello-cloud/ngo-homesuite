from __future__ import annotations

import pytest
from datetime import datetime, UTC

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donation, Organization, User, db


@pytest.fixture(scope="module")
def app():
    return create_app(TestingConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _ensure_user(app, username: str, email: str, role: str, password: str):
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, email=email, role=role, is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()


def test_workflow_and_semantic_routes(client, app):
    _ensure_user(app, "workflow_admin", "workflow_admin@test.local", "admin", "workflow_admin_pass_123")
    _login(client, "workflow_admin", "workflow_admin_pass_123")

    page = client.get("/workflows")
    assert page.status_code == 200

    domain = client.get("/api/domain/snapshot")
    assert domain.status_code == 200
    domain_payload = domain.get_json()
    assert domain_payload["ok"] is True
    assert isinstance(domain_payload["entities"], dict)

    semantic = client.get("/api/semantic/context?task=donor+follow+up")
    assert semantic.status_code == 200
    semantic_payload = semantic.get_json()
    assert semantic_payload["ok"] is True
    assert semantic_payload["context"]["entity_count"] >= 1


def test_workflow_api_runs(client, app):
    _ensure_user(app, "workflow_admin2", "workflow_admin2@test.local", "admin", "workflow_admin2_pass_123")
    _login(client, "workflow_admin2", "workflow_admin2_pass_123")

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        if org is None:
            org = Organization(name="Workflow Org", slug="workflow-org", is_active=True)
            db.session.add(org)
            db.session.commit()

        donation = Donation.query.filter_by(organization_id=org.id).order_by(Donation.id.asc()).first()
        if donation is None:
            donation = Donation(
                organization_id=org.id,
                donor_name="Workflow Seed Donor",
                amount=123.45,
                currency="USD",
                donation_date=datetime.now(UTC),
                status="received",
                purpose="Workflow Test",
            )
            db.session.add(donation)
            db.session.commit()
        donation_id = donation.id

    donation_run = client.post(f"/api/workflows/donation/{donation_id}/run")
    assert donation_run.status_code == 200
    donation_payload = donation_run.get_json()
    assert donation_payload["ok"] is True
    assert donation_payload["workflow"] == "donation_receipt_followup"

    grant_run = client.post(
        "/api/workflows/grant/run",
        json={"grant_name": "Community Grant 2026", "requested_amount": 5000},
    )
    assert grant_run.status_code == 200
    assert grant_run.get_json()["workflow"] == "grant_tracking_reporting"

    program_run = client.post(
        "/api/workflows/program-impact/run",
        json={
            "program_name": "Youth Learning",
            "beneficiary_count": 42,
            "outcomes": [{"metric_name": "attendance", "metric_value": 88.5}],
        },
    )
    assert program_run.status_code == 200
    assert program_run.get_json()["workflow"] == "program_tracking_impact_report"
