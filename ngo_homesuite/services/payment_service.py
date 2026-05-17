"""Stripe payment service.

Responsibilities
----------------
1. Create Stripe Checkout sessions for one-time donations.
2. Handle ``checkout.session.completed`` webhook events — idempotently
   create a Donation record via DonationService.
3. Record direct (non-Stripe) donations for cash/bank-transfer flows.

Design notes
------------
* No Flask app object is created here — this is a plain service module.
* Stripe is an *optional* dependency (in requirements-cloud.txt).  When
  ``stripe`` is not installed the module gracefully degrades: Checkout
  session creation raises ``StripeNotConfigured``; webhook handling falls
  back to manual receipt flow.
* Idempotency: ``checkout.session.completed`` deduplication relies on
  ``Donation.reference_number`` uniqueness.  The reference is the Stripe
  payment-intent ID (or the session ID when no payment-intent is present).
  A duplicate insert is caught and the existing donation is returned.
* Amount conversion: Stripe amounts are in *cents*; the Donation model
  stores *currency units* (dollars).  This module performs the conversion.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ngo_homesuite.models.core import Donation, db
from ngo_homesuite.services.donation_service import DonationConcurrencyError, DonationNotFound, DonationService

logger = logging.getLogger(__name__)

_donation_service = DonationService()

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StripeNotConfigured(Exception):
    """Raised when Stripe credentials are absent."""


class WebhookProcessingError(Exception):
    """Raised when a webhook event cannot be processed."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ALLOWED_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD", "INR", "NGN"}

# Stripe currencies that use zero-decimal units (no cents conversion needed).
# https://docs.stripe.com/currencies#zero-decimal
_ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}


def _cents_to_amount(amount_cents: int, currency: str) -> float:
    if currency.upper() in _ZERO_DECIMAL_CURRENCIES:
        return float(amount_cents)
    return round(amount_cents / 100, 2)


def _get_stripe():
    """Import stripe lazily so the app starts even without the library."""
    try:
        import stripe  # noqa: PLC0415
        return stripe
    except ImportError:
        raise StripeNotConfigured(
            "The 'stripe' package is not installed. "
            "Run: pip install stripe  (or add it to requirements-cloud.txt)"
        )


def _stripe_api_key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise StripeNotConfigured(
            "STRIPE_SECRET_KEY environment variable is not set."
        )
    return key


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PaymentService:
    """High-level payment operations."""

    # ------------------------------------------------------------------
    # Checkout session creation
    # ------------------------------------------------------------------

    def create_checkout_session(
        self,
        *,
        org_id: int,
        donor_id: Optional[int],
        campaign_id: Optional[int],
        amount_cents: int,
        currency: str,
        campaign_name: str,
        success_url: str,
        cancel_url: str,
        donor_email: Optional[str] = None,
        donor_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a Stripe Checkout Session for a one-time donation.

        Returns a dict with ``checkout_url`` and ``session_id``.

        Raises:
            StripeNotConfigured: Stripe library or secret key missing.
            ValueError: Invalid amount or currency.
        """
        stripe = _get_stripe()
        stripe.api_key = _stripe_api_key()

        currency = currency.upper()
        if currency not in _ALLOWED_CURRENCIES:
            raise ValueError(
                f"Currency {currency!r} is not in the allowed set: {sorted(_ALLOWED_CURRENCIES)}"
            )
        if amount_cents <= 0:
            raise ValueError(f"amount_cents must be positive, got {amount_cents}")
        if not success_url.startswith("https://") and not success_url.startswith("http://"):
            raise ValueError("success_url must be a valid HTTP/HTTPS URL")
        if not cancel_url.startswith("https://") and not cancel_url.startswith("http://"):
            raise ValueError("cancel_url must be a valid HTTP/HTTPS URL")

        metadata: dict[str, str] = {"org_id": str(org_id)}
        if donor_id is not None:
            metadata["donor_id"] = str(donor_id)
        if campaign_id is not None:
            metadata["campaign_id"] = str(campaign_id)

        session_params: dict[str, Any] = {
            "payment_method_types": ["card"],
            "line_items": [
                {
                    "price_data": {
                        "currency": currency.lower(),
                        "product_data": {"name": campaign_name},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata,
        }
        if donor_email:
            session_params["customer_email"] = donor_email

        session = stripe.checkout.Session.create(**session_params)
        logger.info(
            "Stripe checkout session created",
            extra={
                "session_id": session.id,
                "org_id": org_id,
                "amount_cents": amount_cents,
                "currency": currency,
            },
        )
        return {"checkout_url": session.url, "session_id": session.id}

    # ------------------------------------------------------------------
    # Webhook handler
    # ------------------------------------------------------------------

    def handle_checkout_completed(
        self,
        session_obj: dict[str, Any],
        *,
        org_id: Optional[int] = None,
    ) -> Donation:
        """Idempotently process a ``checkout.session.completed`` Stripe event.

        Creates a Donation record if one doesn't already exist for the
        payment reference.  Uses the payment_intent ID as the idempotency
        key (falls back to session ID when no payment_intent is present).

        Args:
            session_obj: The ``data.object`` dict from the Stripe event.
            org_id: Organisation ID.  Taken from session metadata when None.

        Returns:
            The created or pre-existing Donation record.

        Raises:
            WebhookProcessingError: If required fields are missing or the
                                    donation cannot be persisted.
        """
        payment_status = session_obj.get("payment_status")
        if payment_status != "paid":
            raise WebhookProcessingError(
                f"Session {session_obj.get('id')} has payment_status={payment_status!r}; "
                "expected 'paid'. No donation created."
            )

        metadata = session_obj.get("metadata") or {}
        if org_id is None:
            raw_org_id = metadata.get("org_id")
            if not raw_org_id:
                raise WebhookProcessingError(
                    "org_id not found in session metadata and not supplied by caller."
                )
            try:
                org_id = int(raw_org_id)
            except (ValueError, TypeError):
                raise WebhookProcessingError(
                    f"Invalid org_id in session metadata: {raw_org_id!r}"
                )

        # Build idempotency reference
        payment_intent = session_obj.get("payment_intent")
        session_id = session_obj.get("id", "")
        reference_number = payment_intent or session_id
        if not reference_number:
            raise WebhookProcessingError("Cannot determine reference_number from Stripe session.")

        # Check for existing donation (idempotency)
        existing = db.session.scalars(
            select(Donation).where(
                Donation.reference_number == reference_number,
                Donation.organization_id == org_id,
            ).limit(1)
        ).first()
        if existing:
            logger.info(
                "Duplicate checkout.session.completed — donation already exists",
                extra={"reference_number": reference_number, "donation_id": existing.id},
            )
            return existing

        # Extract amounts
        amount_cents = session_obj.get("amount_total")
        if not isinstance(amount_cents, int) or amount_cents <= 0:
            raise WebhookProcessingError(
                f"Invalid amount_total in session: {amount_cents!r}"
            )
        currency = str(session_obj.get("currency") or "usd").upper()
        amount = _cents_to_amount(amount_cents, currency)

        # Extract optional donor info
        donor_name = session_obj.get("customer_details", {}).get("name") or metadata.get("donor_name") or "Anonymous"
        donor_email = session_obj.get("customer_details", {}).get("email") or metadata.get("donor_email")

        donor_id: Optional[int] = None
        raw_donor_id = metadata.get("donor_id")
        if raw_donor_id:
            try:
                donor_id = int(raw_donor_id)
            except (ValueError, TypeError):
                logger.warning("Invalid donor_id in Stripe metadata: %s", raw_donor_id)

        campaign_id: Optional[int] = None
        raw_project_id = metadata.get("campaign_id")  # legacy field name in metadata
        if raw_project_id:
            try:
                campaign_id = int(raw_project_id)
            except (ValueError, TypeError):
                logger.warning("Invalid campaign_id in Stripe metadata: %s", raw_project_id)

        try:
            donation = _donation_service.create_donation(
                org_id=org_id,
                donor_name=donor_name,
                amount=amount,
                currency=currency,
                donor_email=donor_email,
                donor_id=donor_id,
                campaign_id=campaign_id,
                payment_method="stripe",
                reference_number=reference_number,
                purpose=f"Online donation via Stripe ({session_id})",
                status="received",
            )
        except IntegrityError:
            # Race condition: another worker already inserted with same reference_number
            db.session.rollback()
            donation = db.session.scalars(
                select(Donation).where(
                    Donation.reference_number == reference_number,
                    Donation.organization_id == org_id,
                ).limit(1)
            ).first()
            if donation is None:
                raise WebhookProcessingError(
                    f"IntegrityError inserting donation with reference {reference_number!r} "
                    "but no existing record found — data may be inconsistent."
                )
            logger.info(
                "Race-condition idempotency: donation already inserted",
                extra={"reference_number": reference_number, "donation_id": donation.id},
            )
        except (ValueError, DonationConcurrencyError) as exc:
            raise WebhookProcessingError(
                f"Failed to create donation from Stripe webhook: {exc}"
            ) from exc

        logger.info(
            "Donation created from Stripe checkout.session.completed",
            extra={
                "donation_id": donation.id,
                "org_id": org_id,
                "amount": amount,
                "currency": currency,
                "reference_number": reference_number,
            },
        )
        return donation

    # ------------------------------------------------------------------
    # Direct (non-Stripe) donation recording
    # ------------------------------------------------------------------

    def record_direct_donation(
        self,
        org_id: int,
        donor_name: str,
        amount: float,
        *,
        currency: str = "USD",
        payment_method: str = "cash",
        donor_email: Optional[str] = None,
        donor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        fund_id: Optional[int] = None,
        purpose: Optional[str] = None,
        notes: Optional[str] = None,
        reference_number: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> Donation:
        """Record a cash/bank-transfer/cheque donation directly (no Stripe).

        Delegates to DonationService.create_donation with status='received'.
        """
        return _donation_service.create_donation(
            org_id=org_id,
            donor_name=donor_name,
            amount=amount,
            currency=currency,
            donor_email=donor_email,
            donor_id=donor_id,
            project_id=project_id,
            fund_id=fund_id,
            payment_method=payment_method,
            reference_number=reference_number,
            purpose=purpose,
            notes=notes,
            status="received",
            actor_id=actor_id,
        )
