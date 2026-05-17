from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Organization, db


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
