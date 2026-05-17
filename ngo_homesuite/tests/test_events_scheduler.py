from __future__ import annotations

from datetime import datetime

from ngo_homesuite.events import scheduler as scheduler_module
from ngo_homesuite.events import services as events_services


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


def test_send_event_reminder_uses_email_utility(monkeypatch):
    monkeypatch.setattr(events_services, "get_event", lambda _event_id: {"title": "Town Hall"})
    called = {"ok": False}

    def _fake_send_email(**kwargs):
        called["ok"] = kwargs.get("to") == "attendee@example.org"
        return True

    monkeypatch.setattr(events_services, "send_email", _fake_send_email)
    assert events_services.send_event_reminder(10, "attendee@example.org", "Attendee") is True
    assert called["ok"] is True


def test_send_due_event_reminders_selects_window(monkeypatch):
    monkeypatch.setattr(events_services, "_utcnow", lambda: datetime(2026, 5, 20, 10, 0, 0))
    monkeypatch.setattr(
        events_services.db.session,
        "execute",
        lambda *_args, **_kwargs: _FakeResult(
            [
                {"id": 1, "name": "In Window", "start_date": "2026-05-21T10:05:00"},
                {"id": 2, "name": "Out Window", "start_date": "2026-05-21T12:00:00"},
            ]
        ),
    )
    monkeypatch.setattr(events_services, "_attendee_emails", lambda event_id: [("a@example.org", "A")] if event_id == 1 else [])
    monkeypatch.setattr(events_services, "send_event_reminder", lambda *_args, **_kwargs: True)

    result = events_services.send_due_event_reminders(hours_before=24)
    assert result == {"matched_events": 1, "sent": 1, "failed": 0}


def test_start_event_reminder_scheduler_registers_jobs(monkeypatch):
    scheduler_module._scheduler = None

    class _FakeScheduler:
        def __init__(self, timezone):
            self.timezone = timezone
            self.jobs = []
            self.started = False

        def add_job(self, func, trigger, minutes, kwargs, id, replace_existing):
            self.jobs.append({"func": func, "trigger": trigger, "minutes": minutes, "kwargs": kwargs, "id": id, "replace_existing": replace_existing})

        def start(self):
            self.started = True

        def shutdown(self, wait=False):
            self.started = False

    monkeypatch.setattr(scheduler_module, "BackgroundScheduler", _FakeScheduler)

    class _App:
        config = {}

        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def app_context(self):
            return self._Ctx()

    app = _App()
    scheduler_module.start_event_reminder_scheduler(app)
    try:
        assert scheduler_module._scheduler is not None
        assert len(scheduler_module._scheduler.jobs) == 2
        job_ids = {job["id"] for job in scheduler_module._scheduler.jobs}
        assert job_ids == {"event-reminders-24h", "event-reminders-1h"}
    finally:
        scheduler_module.stop_event_reminder_scheduler()
