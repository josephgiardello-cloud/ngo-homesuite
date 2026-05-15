from __future__ import annotations

import hashlib
import hmac
import json

from ngo_homesuite.utils.payment_webhooks import ReplayGuard, event_id, verify_stripe_signature



def _stripe_header(payload: bytes, secret: str, ts: int) -> str:
    msg = f"{ts}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"



def test_verify_stripe_signature_accepts_valid_event():
    payload_dict = {"id": "evt_123", "type": "checkout.session.completed"}
    payload = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    secret = "whsec_test"
    now = 1_700_000_000
    header = _stripe_header(payload, secret, now)

    event = verify_stripe_signature(payload, header, secret, tolerance_seconds=300, now_ts=now)

    assert event is not None
    assert event["id"] == "evt_123"



def test_verify_stripe_signature_rejects_invalid_signature():
    payload = b'{"id":"evt_bad"}'
    secret = "whsec_test"
    header = "t=1700000000,v1=deadbeef"

    event = verify_stripe_signature(payload, header, secret, tolerance_seconds=300, now_ts=1_700_000_000)

    assert event is None



def test_verify_stripe_signature_rejects_stale_timestamp():
    payload = b'{"id":"evt_old"}'
    secret = "whsec_test"
    header = _stripe_header(payload, secret, 1_700_000_000)

    event = verify_stripe_signature(payload, header, secret, tolerance_seconds=120, now_ts=1_700_000_500)

    assert event is None



def test_replay_guard_detects_duplicate_event_ids():
    guard = ReplayGuard(ttl_seconds=60)
    assert guard.is_replay("evt_1", now_ts=1_000) is False
    assert guard.is_replay("evt_1", now_ts=1_010) is True
    assert guard.is_replay("evt_1", now_ts=1_070) is False



def test_event_id_helper():
    assert event_id({"id": "evt_abc"}) == "evt_abc"
    assert event_id({"type": "x"}) is None
    assert event_id(None) is None
