from __future__ import annotations

import types

import pytest

from ngo_homesuite.services import payment_service
from ngo_homesuite.services.payment_service import PaymentService, WebhookProcessingError


def test_cents_to_amount_handles_zero_decimal_currency() -> None:
    assert payment_service._cents_to_amount(2500, "JPY") == 2500.0
    assert payment_service._cents_to_amount(2500, "USD") == 25.0


def test_create_checkout_session_validates_input(monkeypatch: pytest.MonkeyPatch) -> None:
    service = PaymentService()

    class _SessionApi:
        @staticmethod
        def create(**kwargs):
            return types.SimpleNamespace(id="sess_123", url="https://checkout.test/session")

    fake_stripe = types.SimpleNamespace(checkout=types.SimpleNamespace(Session=_SessionApi), api_key="")
    monkeypatch.setattr(payment_service, "_get_stripe", lambda: fake_stripe)
    monkeypatch.setattr(payment_service, "_stripe_api_key", lambda: "sk_test_123")

    with pytest.raises(ValueError, match="allowed set"):
        service.create_checkout_session(
            org_id=1,
            donor_id=None,
            campaign_id=None,
            amount_cents=100,
            currency="XYZ",
            campaign_name="Campaign",
            success_url="https://example.org/success",
            cancel_url="https://example.org/cancel",
        )

    with pytest.raises(ValueError, match="must be positive"):
        service.create_checkout_session(
            org_id=1,
            donor_id=None,
            campaign_id=None,
            amount_cents=0,
            currency="USD",
            campaign_name="Campaign",
            success_url="https://example.org/success",
            cancel_url="https://example.org/cancel",
        )

    with pytest.raises(ValueError, match="success_url"):
        service.create_checkout_session(
            org_id=1,
            donor_id=None,
            campaign_id=None,
            amount_cents=100,
            currency="USD",
            campaign_name="Campaign",
            success_url="ftp://invalid",
            cancel_url="https://example.org/cancel",
        )


def test_create_checkout_session_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = PaymentService()
    captured: dict[str, object] = {}

    class _SessionApi:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(id="sess_live", url="https://checkout.test/live")

    fake_stripe = types.SimpleNamespace(checkout=types.SimpleNamespace(Session=_SessionApi), api_key="")
    monkeypatch.setattr(payment_service, "_get_stripe", lambda: fake_stripe)
    monkeypatch.setattr(payment_service, "_stripe_api_key", lambda: "sk_live_123")

    result = service.create_checkout_session(
        org_id=77,
        donor_id=5,
        campaign_id=9,
        amount_cents=5500,
        currency="USD",
        campaign_name="Spring Appeal",
        success_url="https://example.org/success",
        cancel_url="https://example.org/cancel",
        donor_email="donor@example.org",
        donor_name="Test Donor",
    )

    assert result["session_id"] == "sess_live"
    assert result["checkout_url"].startswith("https://checkout.test/")
    assert captured["metadata"]["org_id"] == "77"
    assert captured["metadata"]["donor_id"] == "5"
    assert captured["metadata"]["campaign_id"] == "9"


def test_handle_checkout_completed_rejects_unpaid_session() -> None:
    service = PaymentService()
    with pytest.raises(WebhookProcessingError, match="payment_status"):
        service.handle_checkout_completed({"id": "cs_1", "payment_status": "open"}, org_id=1)


def test_handle_checkout_completed_requires_org_id_when_missing_metadata() -> None:
    service = PaymentService()
    session_obj = {
        "id": "cs_1",
        "payment_status": "paid",
        "metadata": {},
        "amount_total": 100,
        "currency": "usd",
    }
    with pytest.raises(WebhookProcessingError, match="org_id"):
        service.handle_checkout_completed(session_obj)


def test_handle_checkout_completed_rejects_invalid_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    service = PaymentService()

    class _DummyScalars:
        @staticmethod
        def first():
            return None

    monkeypatch.setattr(payment_service.db, "session", types.SimpleNamespace(scalars=lambda *_args, **_kwargs: _DummyScalars()))

    session_obj = {
        "id": "cs_1",
        "payment_status": "paid",
        "metadata": {"org_id": "1"},
        "amount_total": "100",
        "currency": "usd",
    }
    with pytest.raises(WebhookProcessingError, match="Invalid amount_total"):
        service.handle_checkout_completed(session_obj)


def test_handle_charge_refunded_missing_payment_intent_returns_none() -> None:
    service = PaymentService()
    assert service.handle_charge_refunded({}) is None


def test_handle_payment_failed_missing_payment_intent_id_returns_none() -> None:
    service = PaymentService()
    assert service.handle_payment_failed({}) is None


def test_record_direct_donation_delegates_to_donation_service(monkeypatch: pytest.MonkeyPatch) -> None:
    service = PaymentService()

    class _Donation:
        id = 99

    captured: dict[str, object] = {}

    def _fake_create_donation(**kwargs):
        captured.update(kwargs)
        return _Donation()

    monkeypatch.setattr(payment_service, "_donation_service", types.SimpleNamespace(create_donation=_fake_create_donation))

    donation = service.record_direct_donation(
        org_id=7,
        donor_name="Donor",
        amount=10.5,
        currency="USD",
        payment_method="cash",
        donor_email="donor@example.org",
    )

    assert donation.id == 99
    assert captured["status"] == "received"
    assert captured["org_id"] == 7
