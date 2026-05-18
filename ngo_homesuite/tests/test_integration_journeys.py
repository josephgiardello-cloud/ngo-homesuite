from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donation, DonationReceipt, Organization


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        SECRET_KEY = "test-secret"

    return create_app(_TestCfg)


@pytest.fixture()
def client(app):
    return app.test_client()


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
