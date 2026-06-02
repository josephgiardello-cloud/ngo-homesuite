import sys
import types

from ngo_homesuite.utils import integrity_drift


def test_anchor_public_timestamp_missing_hash_returns_reason() -> None:
    result = integrity_drift.anchor_seal_public_timestamp({})
    assert result["anchored"] is False
    assert result["reason"] == "missing_hash"


def test_anchor_public_timestamp_success_with_fake_calendar(monkeypatch) -> None:
    submitted_payloads: list[bytes] = []

    class _FakeRemoteCalendar:
        def __init__(self, _url: str) -> None:
            self.url = _url

        def submit(self, msg_bytes: bytes) -> None:
            submitted_payloads.append(msg_bytes)

    fake_calendar_mod = types.ModuleType("opentimestamps.calendar")
    fake_calendar_mod.RemoteCalendar = _FakeRemoteCalendar

    fake_pkg = types.ModuleType("opentimestamps")
    fake_pkg.calendar = fake_calendar_mod

    monkeypatch.setitem(sys.modules, "opentimestamps", fake_pkg)
    monkeypatch.setitem(sys.modules, "opentimestamps.calendar", fake_calendar_mod)

    result = integrity_drift.anchor_seal_public_timestamp({"hash": "ab" * 32})

    assert result["anchored"] is True
    assert result["attempted"] >= 1
    assert len(result["submitted"]) >= 1
    assert all(payload == bytes.fromhex("ab" * 32) for payload in submitted_payloads)
