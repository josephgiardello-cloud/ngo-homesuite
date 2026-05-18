from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from ngo_homesuite.events.services import send_due_event_reminders
from ngo_homesuite.services.campaign_email_service import process_scheduled_campaign_email_batches

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


def _run_scheduled_campaign_batches(app: Flask) -> None:
    with app.app_context():
        limit = int(app.config.get("CAMPAIGN_EMAIL_SCHEDULER_BATCH_LIMIT", 100) or 100)
        result = process_scheduled_campaign_email_batches(limit=limit)
        logger.info(
            "scheduled campaign email batches processed",
            extra={
                "event_id": "campaign.email.scheduled.process",
                "extra_fields": {
                    "processed_batches": result.get("processed_batches", 0),
                    "sent_batches": result.get("sent_batches", 0),
                    "failed_batches": result.get("failed_batches", 0),
                    "emails_sent": result.get("emails_sent", 0),
                    "emails_failed": result.get("emails_failed", 0),
                },
            },
        )


def start_event_reminder_scheduler(app: Flask) -> None:
    global _scheduler
    if _scheduler is not None:
        return

    scheduler = BackgroundScheduler(timezone="UTC")
    event_jobs_enabled = bool(app.config.get("EVENT_REMINDER_SCHEDULER_ENABLED", True))
    campaign_jobs_enabled = bool(app.config.get("CAMPAIGN_EMAIL_SCHEDULER_ENABLED", False))

    if event_jobs_enabled:
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

    if campaign_jobs_enabled:
        interval_minutes = int(app.config.get("CAMPAIGN_EMAIL_SCHEDULER_INTERVAL_MINUTES", 5) or 5)
        scheduler.add_job(
            _run_scheduled_campaign_batches,
            "interval",
            minutes=max(1, interval_minutes),
            kwargs={"app": app},
            id="campaign-email-scheduled-dispatch",
            replace_existing=True,
        )

    if not scheduler.get_jobs():
        return

    scheduler.start()
    _scheduler = scheduler


def stop_event_reminder_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
