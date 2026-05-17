from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from ngo_homesuite.models.core import db
from ngo_homesuite.utils.email import send_email

_MAX_SEND_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 1.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_event_time(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text_value = str(raw).strip()
    if not text_value:
        return None
    # Support both date-only and datetime strings.
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.strptime(text_value, "%Y-%m-%d")
    except ValueError:
        return None


def get_event(event_id: int) -> dict[str, Any] | None:
    row = db.session.execute(
        text(
            """
            SELECT id, name, description, start_date, end_date
            FROM events
            WHERE id = :event_id AND deleted_at IS NULL
            """
        ),
        {"event_id": int(event_id)},
    ).mappings().first()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "title": str(row["name"] or "Event"),
        "description": row["description"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
    }


def _attendee_emails(event_id: int) -> list[tuple[str, str]]:
    rows = db.session.execute(
        text(
            """
            SELECT DISTINCT d.email AS email, COALESCE(d.name, 'Attendee') AS name
            FROM registrations r
            JOIN donors d ON d.id = r.donor_id
            WHERE r.event_id = :event_id
              AND r.deleted_at IS NULL
              AND d.email IS NOT NULL
              AND d.email != ''
            """
        ),
        {"event_id": int(event_id)},
    ).mappings().all()
    return [(str(r["email"]), str(r["name"])) for r in rows]


def send_event_reminder(event_id: int, attendee_email: str, attendee_name: str) -> bool:
    """Send a reminder email with up to _MAX_SEND_ATTEMPTS attempts and exponential backoff."""
    event = get_event(event_id)
    if event is None:
        return False
    for attempt in range(_MAX_SEND_ATTEMPTS):
        try:
            result = send_email(
                to=attendee_email,
                subject=f"Reminder: {event['title']} starting soon",
                template="emails/event_reminder.txt",
                context={"event": event, "name": attendee_name, "text": f"Reminder: {event['title']}"},
            )
            if result:
                return True
        except Exception:
            pass
        if attempt < _MAX_SEND_ATTEMPTS - 1:
            time.sleep(_RETRY_BASE_SECONDS * (2 ** attempt))
    return False


def send_due_event_reminders(*, hours_before: int) -> dict[str, int]:
    now = _utcnow()
    lower = now + timedelta(hours=hours_before) - timedelta(minutes=20)
    upper = now + timedelta(hours=hours_before) + timedelta(minutes=20)

    events = db.session.execute(
        text(
            """
            SELECT id, name, start_date
            FROM events
            WHERE deleted_at IS NULL
              AND start_date IS NOT NULL
            """
        )
    ).mappings().all()

    sent = 0
    failed = 0
    matched_events = 0
    for row in events:
        event_time = _parse_event_time(row["start_date"])
        if event_time is None or event_time < lower or event_time > upper:
            continue
        matched_events += 1
        attendees = _attendee_emails(int(row["id"]))
        for email, name in attendees:
            if send_event_reminder(int(row["id"]), email, name):
                sent += 1
            else:
                failed += 1

    return {
        "matched_events": matched_events,
        "sent": sent,
        "failed": failed,
    }
