from __future__ import annotations

from ngo_homesuite.events import services as events_services


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


def test_process_queue_retries_with_exponential_backoff(monkeypatch):
    captured_sql: list[str] = []

    monkeypatch.setattr(events_services, "_ensure_email_tables", lambda: None)
    monkeypatch.setattr(events_services, "_is_suppressed", lambda _email: False)
    monkeypatch.setattr(events_services, "send_event_reminder", lambda *_args, **_kwargs: False)

    def _fake_execute(statement, params=None):
        sql = str(statement)
        captured_sql.append(sql)
        if "FROM event_email_queue" in sql:
            return _FakeResult(
                [
                    {
                        "id": 10,
                        "event_id": 7,
                        "attendee_email": "attendee@example.org",
                        "attendee_name": "Attendee",
                        "attempt_count": 0,
                        "max_attempts": 3,
                    }
                ]
            )
        return _FakeResult([])

    monkeypatch.setattr(events_services.db.session, "execute", _fake_execute)
    monkeypatch.setattr(events_services.db.session, "commit", lambda: None)

    result = events_services.process_event_email_queue(limit=10)

    assert result["processed"] == 1
    assert result["sent"] == 0
    assert result["retried"] == 1
    assert any("status = 'retrying'" in sql for sql in captured_sql)


def test_process_queue_marks_failed_after_max_attempts(monkeypatch):
    captured_sql: list[str] = []

    monkeypatch.setattr(events_services, "_ensure_email_tables", lambda: None)
    monkeypatch.setattr(events_services, "_is_suppressed", lambda _email: False)
    monkeypatch.setattr(events_services, "send_event_reminder", lambda *_args, **_kwargs: False)

    def _fake_execute(statement, params=None):
        sql = str(statement)
        captured_sql.append(sql)
        if "FROM event_email_queue" in sql:
            return _FakeResult(
                [
                    {
                        "id": 11,
                        "event_id": 8,
                        "attendee_email": "attendee2@example.org",
                        "attendee_name": "Attendee2",
                        "attempt_count": 2,
                        "max_attempts": 3,
                    }
                ]
            )
        return _FakeResult([])

    monkeypatch.setattr(events_services.db.session, "execute", _fake_execute)
    monkeypatch.setattr(events_services.db.session, "commit", lambda: None)

    result = events_services.process_event_email_queue(limit=10)

    assert result["processed"] == 1
    assert result["failed"] == 1
    assert any("status = 'failed'" in sql for sql in captured_sql)


def test_opt_out_suppresses_email(monkeypatch):
    monkeypatch.setattr(events_services, "_ensure_email_tables", lambda: None)
    called = {"value": False}

    def _fake_execute(statement, params=None):
        sql = str(statement)
        if "FROM event_email_queue" in sql:
            return _FakeResult([{"attendee_email": "optout@example.org"}])
        return _FakeResult([])

    def _fake_mark_email_bounced(email: str, *, reason: str = "bounce"):
        called["value"] = email == "optout@example.org" and reason == "opt_out"

    monkeypatch.setattr(events_services.db.session, "execute", _fake_execute)
    monkeypatch.setattr(events_services, "mark_email_bounced", _fake_mark_email_bounced)

    assert events_services.process_email_opt_out("token-123") is True
    assert called["value"] is True
