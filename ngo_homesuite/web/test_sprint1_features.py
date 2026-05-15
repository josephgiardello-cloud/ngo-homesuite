from __future__ import annotations

from datetime import date

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import (
    Donation,
    DonationReceipt,
    Donor,
    Expense,
    Organization,
    P2PPage,
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


def test_public_p2p_page_renders_html_and_json(client, app):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="P2P Public Donor",
            email="p2p.public@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title="Spring Field Kits",
            story="Funding community field kits for youth volunteers.",
            goal_amount=500.0,
            public_slug="spring-field-kits",
            status="active",
        )
        db.session.add(page)
        db.session.commit()

    html_resp = client.get("/p2p/spring-field-kits", headers={"Accept": "text/html"})
    assert html_resp.status_code == 200
    html_body = html_resp.get_data(as_text=True)
    assert "Spring Field Kits" in html_body
    assert "Support this fundraiser" in html_body
    assert "Embed This Fundraiser" in html_body

    json_resp = client.get("/p2p/spring-field-kits", headers={"Accept": "application/json"})
    assert json_resp.status_code == 200
    payload = json_resp.get_json()
    assert payload["title"] == "Spring Field Kits"
    assert "progress" in payload


def test_public_p2p_embed_script_endpoint(client, app):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="P2P Embed Donor",
            email="p2p.embed@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title="Embed Fundraiser",
            goal_amount=300.0,
            public_slug="embed-fundraiser",
            status="active",
        )
        db.session.add(page)
        db.session.commit()

    rv = client.get("/p2p/embed-fundraiser/embed.js")
    assert rv.status_code == 200
    assert rv.mimetype == "application/javascript"
    body = rv.get_data(as_text=True)
    assert "createElement('iframe')" in body
    assert "/p2p/embed-fundraiser?embed=1" in body


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
        refreshed = db.session.get(RecurringDonationPlan, plan_id)
        assert refreshed.status == "failed"
        assert refreshed.fail_count >= 1
        assert refreshed.last_error


def test_donor_detail_page_shows_profile_metrics(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Detail Donor",
            email="detail.donor@example.org",
            donor_type="individual",
            notes="Long-time supporter",
        )
        db.session.add(donor)
        db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=55.0,
            currency="USD",
            status="received",
            payment_method="bank_transfer",
            purpose="Education",
        )
        db.session.add(donation)

        plan = RecurringDonationPlan(
            organization_id=org.id,
            donor_id=donor.id,
            amount=15.0,
            currency="USD",
            frequency="monthly",
            payment_method="credit_card",
            next_charge_date=date.today(),
            status="active",
        )
        db.session.add(plan)
        db.session.commit()
        donor_id = donor.id

    rv = client.get(f"/donors/{donor_id}")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Detail Donor" in body
    assert "Long-time supporter" in body
    assert "AI Donor Insights" in body
    assert "Next Best Action:" in body
    assert "Recent Donations" in body
    assert "Recurring Plans" in body


def test_donations_export_iif_returns_payload(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="IIF Donor",
            email="iif.donor@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=42.5,
            currency="USD",
            status="received",
            purpose="IIF Export",
            payment_method="bank_transfer",
        )
        db.session.add(donation)
        db.session.commit()

    rv = client.get("/donations/export/iif")
    assert rv.status_code == 200
    assert rv.mimetype == "text/plain"
    body = rv.get_data(as_text=True)
    assert "!TRNS" in body
    assert "DEPOSIT" in body


def test_expenses_export_iif_returns_payload(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()

        expense = Expense(
            organization_id=org.id,
            amount=18.75,
            currency="USD",
            payee="IIF Vendor",
            description="IIF Expense Export",
        )
        db.session.add(expense)
        db.session.commit()

    rv = client.get("/expenses/export/iif")
    assert rv.status_code == 200
    assert rv.mimetype == "text/plain"
    body = rv.get_data(as_text=True)
    assert "!TRNS" in body
    assert "CHECK" in body
