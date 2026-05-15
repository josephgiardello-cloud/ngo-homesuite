from __future__ import annotations

from datetime import date

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import (
    Donation,
    DonationReceipt,
    Donor,
    Organization,
    RecurringDonationPlan,
    db,
)


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        SECRET_KEY = "test-secret"

    return create_app(_TestCfg)


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


def test_public_give_creates_donation_and_receipt(client, app):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        before = Donation.query.filter_by(organization_id=org.id).count()

    rv = client.post(
        "/give",
        data={
            "donor_name": "Public Supporter",
            "donor_email": "public.supporter@example.org",
            "donor_phone": "+1-555-2000",
            "amount": "25",
            "currency": "USD",
            "payment_method": "credit_card",
            "purpose": "General Fund",
            "make_recurring": "y",
            "recurring_frequency": "monthly",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        after = Donation.query.filter_by(organization_id=org.id).count()
        assert after == before + 1

        donation = Donation.query.filter_by(organization_id=org.id, donor_email="public.supporter@example.org").order_by(Donation.id.desc()).first()
        assert donation is not None

        receipt = DonationReceipt.query.filter_by(donation_id=donation.id).first()
        assert receipt is not None

        plan = RecurringDonationPlan.query.filter_by(organization_id=org.id, donor_id=donation.donor_id).order_by(RecurringDonationPlan.id.desc()).first()
        assert plan is not None
        assert plan.status == "active"


def test_donor_merge_relinks_donations(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()

        primary = Donor(organization_id=org.id, name="Merge Target", email="merge.target@example.org", donor_type="individual")
        duplicate = Donor(organization_id=org.id, name="Merge Target", email="merge.target@example.org", donor_type="individual")
        db.session.add_all([primary, duplicate])
        db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=duplicate.id,
            donor_name=duplicate.name,
            donor_email=duplicate.email,
            amount=33.0,
            currency="USD",
            status="received",
            payment_method="bank_transfer",
        )
        db.session.add(donation)
        db.session.commit()

        primary_id = primary.id
        duplicate_id = duplicate.id
        donation_id = donation.id

    rv = client.post(
        "/donors/merge",
        data={"primary_id": str(primary_id), "duplicate_id": str(duplicate_id)},
        follow_redirects=True,
    )
    assert rv.status_code == 200

    with app.app_context():
        assert Donor.query.filter_by(id=duplicate_id).first() is None
        merged_donation = Donation.query.filter_by(id=donation_id).first()
        assert merged_donation.donor_id == primary_id


def test_process_recurring_marks_failure_without_email(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(organization_id=org.id, name="No Email Recurring", email=None, donor_type="individual")
        db.session.add(donor)
        db.session.flush()

        plan = RecurringDonationPlan(
            organization_id=org.id,
            donor_id=donor.id,
            amount=19.0,
            currency="USD",
            frequency="monthly",
            payment_method="credit_card",
            next_charge_date=date.today(),
            status="active",
        )
        db.session.add(plan)
        db.session.commit()
        plan_id = plan.id

    rv = client.post("/donations/recurring/process", follow_redirects=True)
    assert rv.status_code == 200

    with app.app_context():
        refreshed = RecurringDonationPlan.query.get(plan_id)
        assert refreshed.status == "failed"
        assert refreshed.fail_count >= 1
        assert refreshed.last_error
