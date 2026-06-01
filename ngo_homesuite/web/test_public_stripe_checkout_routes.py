from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Campaign, Donation, Fund, Organization, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _ensure_org(app) -> int:
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        if org is None:
            org = Organization(name="Public Donate Org", slug="public-donate-org", is_active=True)
            db.session.add(org)
            db.session.commit()
        return int(org.id)


def test_public_donate_create_checkout_redirects(client, app, monkeypatch):
    _ensure_org(app)

    monkeypatch.setattr(
        "ngo_homesuite.services.payment_service.PaymentService.create_checkout_session",
        lambda self, **kwargs: {"checkout_url": "https://stripe.test/checkout/session_123", "session_id": "session_123"},
    )

    rv = client.post(
        "/public/donate/create-checkout",
        data={"amount": "12.50", "email": "publicdonor@example.org"},
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert "https://stripe.test/checkout/session_123" in (rv.headers.get("Location") or "")


def test_public_donate_page_embed_script_endpoint(client):
    rv = client.get("/public/donate/embed.js")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "iframe" in body
    assert "/public/donate?embed=1" in body


def test_public_donate_create_checkout_accepts_fund_and_tribute_fields(client, app, monkeypatch):
    org_id = _ensure_org(app)
    with app.app_context():
        fund = Fund(organization_id=org_id, name="Public Giving Fund", is_active=True)
        campaign = Campaign(organization_id=org_id, name="Public Give Campaign", slug="public-give-campaign", status="active")
        db.session.add_all([fund, campaign])
        db.session.commit()
        fund_id = int(fund.id)
        campaign_id = int(campaign.id)

    monkeypatch.setattr(
        "ngo_homesuite.services.payment_service.PaymentService.create_checkout_session",
        lambda self, **kwargs: {"checkout_url": "https://stripe.test/checkout/session_tribute", "session_id": "session_tribute"},
    )

    rv = client.post(
        "/public/donate/create-checkout",
        data={
            "amount": "65.00",
            "email": "tributegiver@example.org",
            "donor_name": "Tribute Giver",
            "campaign_id": str(campaign_id),
            "fund_id": str(fund_id),
            "purpose": "Scholarship support",
            "tribute_type": "in_honor_of",
            "tribute_honoree_name": "Coach Rivera",
            "tribute_honoree_contact": "coach@example.org",
        },
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert "https://stripe.test/checkout/session_tribute" in (rv.headers.get("Location") or "")

    with app.app_context():
        donation = db.session.scalars(
            db.select(Donation)
            .where(Donation.organization_id == org_id, Donation.donor_email == "tributegiver@example.org")
            .order_by(Donation.id.desc())
            .limit(1)
        ).first()
        assert donation is not None
        assert int(donation.fund_id or 0) == fund_id
        assert int(donation.campaign_id or 0) == campaign_id
        assert donation.tribute_type == "in_honor_of"
        assert donation.tribute_honoree_name == "Coach Rivera"
        assert donation.tribute_honoree_contact == "coach@example.org"
        assert donation.purpose == "Scholarship support"
