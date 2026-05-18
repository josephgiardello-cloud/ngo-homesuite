from __future__ import annotations

import json
import sqlite3

import ngo_homesuite.db.utils as db_utils


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))


class _FakeConnNoCommit:
    def commit(self) -> None:
        raise AssertionError("audit op should not call conn.commit directly")


def test_audit_uses_run_db_write_transaction(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_db(op, *, write=False, **_kwargs):
        captured["write"] = write
        cur = _FakeCursor()
        op(_FakeConnNoCommit(), cur)
        captured["calls"] = cur.calls
        return None

    monkeypatch.setattr(db_utils, "run_db", _fake_run_db)
    monkeypatch.setattr(db_utils, "CURRENT_USER", {"id": 42, "username": "alice", "role": "admin"})

    db_utils.audit("donor.create", entity_type="donor", entity_id=7, details={"name": "A"})

    assert captured["write"] is True
    calls = captured["calls"]
    assert isinstance(calls, list)
    assert len(calls) == 1
    _, params = calls[0]
    details_json = params[-1]
    assert isinstance(details_json, str)
    envelope = json.loads(details_json)
    assert envelope["trace"]["schema_version"] == 1
    assert envelope["trace"]["request_id"] is None
    assert envelope["payload"] == {"name": "A"}


def test_audit_includes_request_id_when_request_context_active(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_db(op, *, write=False, **_kwargs):
        captured["write"] = write
        cur = _FakeCursor()
        op(_FakeConnNoCommit(), cur)
        captured["calls"] = cur.calls
        return None

    monkeypatch.setattr(db_utils, "run_db", _fake_run_db)
    monkeypatch.setattr(db_utils, "has_request_context", lambda: True)
    monkeypatch.setattr(db_utils, "get_request_id", lambda: "req-123")

    db_utils.audit("donor.update", details={"status": "ok"})

    calls = captured["calls"]
    assert isinstance(calls, list)
    assert len(calls) == 1
    _, params = calls[0]
    envelope = json.loads(params[-1])
    assert envelope["trace"]["request_id"] == "req-123"
    assert envelope["payload"] == {"status": "ok"}


def test_audit_handles_non_serializable_details(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_db(op, *, write=False, **_kwargs):
        cur = _FakeCursor()
        op(_FakeConnNoCommit(), cur)
        captured["calls"] = cur.calls
        return None

    monkeypatch.setattr(db_utils, "run_db", _fake_run_db)

    db_utils.audit("donor.update", details={"bad": {1, 2, 3}})

    calls = captured["calls"]
    assert isinstance(calls, list)
    _, params = calls[0]
    envelope = json.loads(params[-1])
    assert envelope["payload"]["serialization_error"] is True


def test_audit_ignores_invalid_action(monkeypatch):
    called = False

    def _fake_run_db(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(db_utils, "run_db", _fake_run_db)

    db_utils.audit("")
    db_utils.audit("x" * 81)

    assert called is False


def test_audit_swallows_sqlite_errors(monkeypatch):
    def _fake_run_db(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db_utils, "run_db", _fake_run_db)

    db_utils.audit("report.generate", details={"scope": "org"})
