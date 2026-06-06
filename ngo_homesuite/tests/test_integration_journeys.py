from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donation, DonationReceipt, Organization, RecurringDonationPlan, User, db


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        SECRET_KEY = "test-secret"

    return create_app(_TestCfg)


@pytest.fixture()
def client(app):
    return app.test_client()


def _ensure_user(app, *, username: str, email: str, role: str, organization_id: int) -> None:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=email,
                role=role,
                organization_id=organization_id,
                is_active=True,
            )
            user.set_password("JourneyPass123!")
            db.session.add(user)
            db.session.commit()
        else:
            user.role = role
            user.organization_id = organization_id
            user.is_active = True
            user.set_password("JourneyPass123!")
            db.session.commit()


def _login(client, username: str, password: str = "JourneyPass123!") -> None:
    rv = client.post("/auth/login", data={"username": username, "password": password})
    assert rv.status_code in (200, 302)


def _logout(client) -> None:
    client.post("/auth/logout")


def test_donor_to_donation_to_receipt_journey(client, app):
    """Minimal e2e smoke journey: public donation creates a receipted donation."""
    donor_email = "integration.journey@example.org"

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None
        before = Donation.query.filter_by(organization_id=org.id).count()

    response = client.post(
        "/give",
        data={
            "donor_name": "Integration Journey Donor",
            "donor_email": donor_email,
            "donor_phone": "+1-555-2099",
            "amount": "42",
            "currency": "USD",
            "payment_method": "credit_card",
            "purpose": "General Fund",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        after = Donation.query.filter_by(organization_id=org.id).count()
        assert after == before + 1

        donation = (
            Donation.query.filter_by(organization_id=org.id, donor_email=donor_email)
            .order_by(Donation.id.desc())
            .first()
        )
        assert donation is not None

        receipt = DonationReceipt.query.filter_by(donation_id=donation.id).first()
        assert receipt is not None


def test_public_donation_validation_failure_then_recovery(client, app):
    """Invalid donation submission should not persist data, and retrying valid input should succeed."""
    donor_email = "integration.recovery@example.org"

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None
        before = Donation.query.filter_by(organization_id=org.id).count()

    invalid_response = client.post(
        "/give",
        data={
            "donor_name": "Recovery Donor",
            "donor_email": donor_email,
            "donor_phone": "+1-555-3333",
            "amount": "0",
            "currency": "USD",
            "payment_method": "credit_card",
            "purpose": "General Fund",
        },
        follow_redirects=True,
    )
    assert invalid_response.status_code == 200

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        after_invalid = Donation.query.filter_by(organization_id=org.id).count()
        assert after_invalid == before

    valid_response = client.post(
        "/give",
        data={
            "donor_name": "Recovery Donor",
            "donor_email": donor_email,
            "donor_phone": "+1-555-3333",
            "amount": "55",
            "currency": "USD",
            "payment_method": "credit_card",
            "purpose": "General Fund",
        },
        follow_redirects=True,
    )
    assert valid_response.status_code == 200

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        after_valid = Donation.query.filter_by(organization_id=org.id).count()
        assert after_valid == before + 1

        donation = (
            Donation.query.filter_by(organization_id=org.id, donor_email=donor_email)
            .order_by(Donation.id.desc())
            .first()
        )
        assert donation is not None

        receipt = DonationReceipt.query.filter_by(donation_id=donation.id).first()
        assert receipt is not None


def test_public_recurring_donation_plan_creation_journey(client, app):
    """Public give flow with recurring enabled should persist recurring plan and receipt."""
    donor_email = "integration.recurring@example.org"

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None
        before_plans = RecurringDonationPlan.query.filter_by(organization_id=org.id).count()

    response = client.post(
        "/give",
        data={
            "donor_name": "Recurring Journey Donor",
            "donor_email": donor_email,
            "donor_phone": "+1-555-7011",
            "amount": "31",
            "currency": "USD",
            "payment_method": "credit_card",
            "purpose": "Sustainer",
            "make_recurring": "y",
            "recurring_frequency": "monthly",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None

        donation = (
            Donation.query.filter_by(organization_id=org.id, donor_email=donor_email)
            .order_by(Donation.id.desc())
            .first()
        )
        assert donation is not None

        receipt = DonationReceipt.query.filter_by(donation_id=donation.id).first()
        assert receipt is not None

        after_plans = RecurringDonationPlan.query.filter_by(organization_id=org.id).count()
        assert after_plans == before_plans + 1


def test_grant_create_advance_disburse_journey(client, app):
    """Staff grant lifecycle journey should create, advance, and disburse successfully."""
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None
        org_id = int(org.id)

    _ensure_user(
        app,
        username="integration_grant_staff",
        email="integration.grant.staff@example.org",
        role="staff",
        organization_id=org_id,
    )

    _login(client, "integration_grant_staff")

    create_rv = client.post(
        "/grants/",
        json={
            "title": "Integration Journey Grant",
            "funder_name": "Journey Foundation",
        },
    )
    assert create_rv.status_code == 201
    grant_id = int(create_rv.get_json()["id"])

    advance_rv = client.post(
        f"/grants/{grant_id}/advance",
        json={"new_status": "awarded", "amount_awarded": 1500.0},
    )
    assert advance_rv.status_code == 200
    assert str(advance_rv.get_json().get("status") or "") == "awarded"

    disburse_rv = client.post(
        f"/grants/{grant_id}/disburse",
        json={"amount": 1250.0, "received_date": "2026-05-01"},
    )
    assert disburse_rv.status_code == 201
    payload = disburse_rv.get_json() or {}
    assert float(payload.get("amount") or 0.0) >= 1250.0

    _logout(client)
