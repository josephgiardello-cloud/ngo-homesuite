from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any


def _parse_stripe_signature_header(sig_header: str | None) -> tuple[int, list[str]] | None:
    if not sig_header:
        return None

    timestamp: int | None = None
    signatures: list[str] = []
    for chunk in sig_header.split(","):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        return None
    return timestamp, signatures


def verify_stripe_signature(
    payload: bytes,
    sig_header: str | None,
    secret: str | None,
    *,
    tolerance_seconds: int = 300,
    now_ts: int | None = None,
) -> dict[str, Any] | None:
    """Verify Stripe webhook signature and return parsed event JSON when valid."""
    if not secret or not payload:
        return None

    parsed = _parse_stripe_signature_header(sig_header)
    if parsed is None:
        return None
    timestamp, signatures = parsed

    now = int(time.time()) if now_ts is None else int(now_ts)
    if tolerance_seconds >= 0 and abs(now - timestamp) > tolerance_seconds:
        return None

    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        return None

    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def event_id(event: dict[str, Any] | None) -> str | None:
    if not isinstance(event, dict):
        return None
    value = event.get("id")
    return str(value) if value else None


class ReplayGuard:
    """In-memory replay guard for webhook event IDs with TTL."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._seen: dict[str, int] = {}

    def is_replay(self, event_id_value: str | None, *, now_ts: int | None = None) -> bool:
        if not event_id_value:
            return False

        now = int(time.time()) if now_ts is None else int(now_ts)
        expired_before = now - self.ttl_seconds
        for key, ts in list(self._seen.items()):
            if ts < expired_before:
                self._seen.pop(key, None)

        if event_id_value in self._seen:
            return True
        self._seen[event_id_value] = now
        return False


def handle_paypal_webhook(payload: bytes, secret: str) -> str:
    # Basic HMAC verification helper
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

# Example Flask route for Stripe
# @app.route('/webhook/stripe', methods=['POST'])
# def stripe_webhook():
#     event = verify_stripe_signature(request.data, request.headers.get('Stripe-Signature'), STRIPE_SECRET)
#     if event:
#         # Process donation
#         pass
#     return '', 200
