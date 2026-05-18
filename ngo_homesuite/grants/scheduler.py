"""Background scheduler for grant saved-search auto-refresh.

Registers a periodic APScheduler job that runs all active GrantSearchProfile
records and generates new GrantSearchAlert rows for matching opportunities.

Activated via ``GRANT_SEARCH_SCHEDULER_ENABLED=true`` environment variable /
Flask config key.  Interval is tunable via
``GRANT_SEARCH_SCHEDULER_INTERVAL_MINUTES`` (default 60).
"""

from __future__ import annotations

import logging

from flask import Flask

logger = logging.getLogger(__name__)

_scheduler = None  # module-level singleton (BackgroundScheduler | None)


def _run_grant_search_refresh(app: Flask) -> None:
    with app.app_context():
        from ngo_homesuite.grants.services.grants_gov import run_active_saved_search_alerts

        try:
            result = run_active_saved_search_alerts()
            logger.info(
                "grant search refresh complete",
                extra={
                    "event_id": "grants.search.refresh",
                    "extra_fields": {
                        "profiles_run": result.get("profiles_run", 0),
                        "created_alerts": result.get("created_alerts", 0),
                    },
                },
            )
        except Exception as exc:  # pragma: no cover
            logger.error("grant search refresh failed: %s", exc, exc_info=True)


def start_grant_search_scheduler(app: Flask) -> None:
    """Start the background grant search refresh scheduler (idempotent).

    Call from :func:`ngo_homesuite.app_factory.create_app` when
    ``GRANT_SEARCH_SCHEDULER_ENABLED`` is truthy and ``TESTING`` is false.
    """
    global _scheduler
    if _scheduler is not None:
        return

    from apscheduler.schedulers.background import BackgroundScheduler

    interval_minutes = int(app.config.get("GRANT_SEARCH_SCHEDULER_INTERVAL_MINUTES", 60) or 60)
    interval_minutes = max(5, min(1440, interval_minutes))  # clamp 5 min – 24 h

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_grant_search_refresh,
        "interval",
        minutes=interval_minutes,
        kwargs={"app": app},
        id="grant-search-refresh",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    app.logger.info("Grant search refresh scheduler started (interval=%d min)", interval_minutes)
