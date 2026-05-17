from __future__ import annotations

from ngo_homesuite.services import events_service


def test_send_reminder_rejects_missing_email(monkeypatch):
    monkeypatch.setattr(events_service, "_load_event", lambda _eid: {"title": "Town Hall", "start_datetime": "2026-05-20", "location": "HQ"})
    rv = events_service.send_reminder(1, "")
    assert rv["sent"] is False
    assert rv["error"] == "missing_attendee_email"


def test_send_reminder_returns_event_not_found(monkeypatch):
    monkeypatch.setattr(events_service, "_load_event", lambda _eid: None)
    rv = events_service.send_reminder(999, "attendee@example.org")
    assert rv["sent"] is False
    assert rv["error"] == "event_not_found"


def test_send_reminder_uses_sendgrid_when_available(monkeypatch):
    monkeypatch.setattr(events_service, "_load_event", lambda _eid: {"title": "Town Hall", "start_datetime": "2026-05-20", "location": "HQ"})
    monkeypatch.setattr(events_service, "_send_via_sendgrid", lambda **_kwargs: (True, None))
    monkeypatch.setattr(events_service, "_send_via_smtp", lambda **_kwargs: (False, "should_not_call"))

    rv = events_service.send_reminder(1, "attendee@example.org")
    assert rv["sent"] is True
    assert rv["provider"] == "sendgrid"


def test_send_reminder_falls_back_to_smtp(monkeypatch):
    monkeypatch.setattr(events_service, "_load_event", lambda _eid: {"title": "Town Hall", "start_datetime": "2026-05-20", "location": "HQ"})
    monkeypatch.setattr(events_service, "_send_via_sendgrid", lambda **_kwargs: (False, "missing_sendgrid_api_key"))
    monkeypatch.setattr(events_service, "_send_via_smtp", lambda **_kwargs: (True, None))

    rv = events_service.send_reminder(1, "attendee@example.org")
    assert rv["sent"] is True
    assert rv["provider"] == "smtp"
