from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from ngo_homesuite.events.services import send_due_event_reminders

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _run_with_app(app: Flask, *, hours_before: int) -> None:
    with app.app_context():
        result = send_due_event_reminders(hours_before=hours_before)
        logger.info(
            "event reminders dispatched",
            extra={
                "event_id": "events.reminders.dispatch",
                "extra_fields": {
                    "hours_before": hours_before,
                    "matched_events": result.get("matched_events", 0),
                    "sent": result.get("sent", 0),
                    "failed": result.get("failed", 0),
                },
            },
        )


def start_event_reminder_scheduler(app: Flask) -> None:
    global _scheduler
    if _scheduler is not None:
        return

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_with_app,
        "interval",
        minutes=15,
        kwargs={"app": app, "hours_before": 24},
        id="event-reminders-24h",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_with_app,
        "interval",
        minutes=15,
        kwargs={"app": app, "hours_before": 1},
        id="event-reminders-1h",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler


def stop_event_reminder_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
