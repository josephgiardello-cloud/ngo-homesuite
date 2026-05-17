from __future__ import annotations

import time
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from ngo_homesuite.models.core import db
from ngo_homesuite.utils.email import send_email

_MAX_SEND_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 1.0


def _ensure_email_tables() -> None:
    db.session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS event_email_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                attendee_email TEXT NOT NULL,
                attendee_name TEXT NOT NULL,
                hours_before INTEGER NOT NULL,
                scheduled_for TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                next_attempt_at TEXT NOT NULL,
                last_error TEXT,
                opt_out_token TEXT,
                opened_at TEXT,
                clicked_at TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(event_id, attendee_email, hours_before)
            )
            """
        )
    )
    db.session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS email_suppressions (
                email TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
    )
    db.session.commit()


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


def _is_suppressed(email: str) -> bool:
    _ensure_email_tables()
    row = db.session.execute(
        text("SELECT email FROM email_suppressions WHERE email = :email LIMIT 1"),
        {"email": str(email).strip().lower()},
    ).mappings().first()
    return row is not None


def mark_email_bounced(email: str, *, reason: str = "bounce") -> None:
    _ensure_email_tables()
    db.session.execute(
        text(
            """
            INSERT INTO email_suppressions(email, reason)
            VALUES (:email, :reason)
            ON CONFLICT(email) DO UPDATE SET reason = excluded.reason
            """
        ),
        {"email": str(email).strip().lower(), "reason": reason},
    )
    db.session.commit()


def mark_email_complaint(email: str, *, reason: str = "complaint") -> None:
    mark_email_bounced(email, reason=reason)


def process_email_opt_out(token: str) -> bool:
    _ensure_email_tables()
    row = db.session.execute(
        text(
            """
            SELECT attendee_email
            FROM event_email_queue
            WHERE opt_out_token = :token
            LIMIT 1
            """
        ),
        {"token": str(token or "").strip()},
    ).mappings().first()
    if row is None:
        return False
    email = str(row["attendee_email"]).strip().lower()
    mark_email_bounced(email, reason="opt_out")
    return True


def send_event_reminder(event_id: int, attendee_email: str, attendee_name: str) -> bool:
    """Send a reminder email with up to _MAX_SEND_ATTEMPTS attempts and exponential backoff."""
    event = get_event(event_id)
    if event is None:
        return False
    opt_out_token = secrets.token_urlsafe(24)
    opt_out_path = f"/events/reminders/opt-out/{opt_out_token}"
    for attempt in range(_MAX_SEND_ATTEMPTS):
        try:
            result = send_email(
                to=attendee_email,
                subject=f"Reminder: {event['title']} starting soon",
                template="emails/event_reminder.txt",
                context={
                    "event": event,
                    "name": attendee_name,
                    "opt_out_path": opt_out_path,
                    "text": f"Reminder: {event['title']}\n\nOpt-out: {opt_out_path}",
                },
            )
            if result:
                return True
        except Exception:
            pass
        if attempt < _MAX_SEND_ATTEMPTS - 1:
            time.sleep(_RETRY_BASE_SECONDS * (2 ** attempt))
    return False


def queue_due_event_reminders(*, hours_before: int) -> dict[str, int]:
    _ensure_email_tables()
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

    matched_events = 0
    queued = 0
    suppressed = 0
    for row in events:
        event_time = _parse_event_time(row["start_date"])
        if event_time is None or event_time < lower or event_time > upper:
            continue
        matched_events += 1
        event_id = int(row["id"])
        for email, name in _attendee_emails(event_id):
            normalized_email = str(email).strip().lower()
            if _is_suppressed(normalized_email):
                suppressed += 1
                continue
            db.session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO event_email_queue(
                        event_id, attendee_email, attendee_name, hours_before,
                        scheduled_for, status, attempt_count, max_attempts,
                        next_attempt_at, opt_out_token
                    )
                    VALUES(
                        :event_id, :attendee_email, :attendee_name, :hours_before,
                        :scheduled_for, 'pending', 0, :max_attempts,
                        :next_attempt_at, :opt_out_token
                    )
                    """
                ),
                {
                    "event_id": event_id,
                    "attendee_email": normalized_email,
                    "attendee_name": name,
                    "hours_before": int(hours_before),
                    "scheduled_for": event_time.isoformat(),
                    "max_attempts": _MAX_SEND_ATTEMPTS,
                    "next_attempt_at": now.isoformat(),
                    "opt_out_token": secrets.token_urlsafe(24),
                },
            )
            queued += 1

    db.session.commit()
    return {
        "matched_events": matched_events,
        "queued": queued,
        "suppressed": suppressed,
    }


def process_event_email_queue(*, limit: int = 200) -> dict[str, int]:
    _ensure_email_tables()
    now = _utcnow()
    rows = db.session.execute(
        text(
            """
            SELECT id, event_id, attendee_email, attendee_name, attempt_count, max_attempts
            FROM event_email_queue
            WHERE status IN ('pending', 'retrying')
              AND next_attempt_at <= :now_iso
            ORDER BY id ASC
            LIMIT :limit
            """
        ),
        {"now_iso": now.isoformat(), "limit": int(limit)},
    ).mappings().all()

    sent = 0
    failed = 0
    retried = 0
    for row in rows:
        queue_id = int(row["id"])
        event_id = int(row["event_id"])
        email = str(row["attendee_email"])
        name = str(row["attendee_name"])
        attempts = int(row["attempt_count"])
        max_attempts = int(row["max_attempts"])

        if _is_suppressed(email):
            db.session.execute(
                text(
                    "UPDATE event_email_queue SET status = 'suppressed', updated_at = :now_iso WHERE id = :id"
                ),
                {"now_iso": now.isoformat(), "id": queue_id},
            )
            failed += 1
            continue

        ok = send_event_reminder(event_id, email, name)
        if ok:
            db.session.execute(
                text(
                    "UPDATE event_email_queue SET status = 'sent', attempt_count = :attempt_count, updated_at = :now_iso WHERE id = :id"
                ),
                {"attempt_count": attempts + 1, "now_iso": now.isoformat(), "id": queue_id},
            )
            sent += 1
            continue

        next_attempt_count = attempts + 1
        if next_attempt_count >= max_attempts:
            db.session.execute(
                text(
                    """
                    UPDATE event_email_queue
                    SET status = 'failed', attempt_count = :attempt_count, last_error = :last_error, updated_at = :now_iso
                    WHERE id = :id
                    """
                ),
                {
                    "attempt_count": next_attempt_count,
                    "last_error": "delivery_failed_after_retries",
                    "now_iso": now.isoformat(),
                    "id": queue_id,
                },
            )
            failed += 1
        else:
            backoff_seconds = _RETRY_BASE_SECONDS * (2 ** attempts)
            next_attempt_at = (now + timedelta(seconds=backoff_seconds)).isoformat()
            db.session.execute(
                text(
                    """
                    UPDATE event_email_queue
                    SET status = 'retrying', attempt_count = :attempt_count, next_attempt_at = :next_attempt_at,
                        last_error = :last_error, updated_at = :now_iso
                    WHERE id = :id
                    """
                ),
                {
                    "attempt_count": next_attempt_count,
                    "next_attempt_at": next_attempt_at,
                    "last_error": "delivery_failed_retrying",
                    "now_iso": now.isoformat(),
                    "id": queue_id,
                },
            )
            retried += 1

    db.session.commit()
    return {
        "processed": len(rows),
        "sent": sent,
        "failed": failed,
        "retried": retried,
    }


def send_due_event_reminders(*, hours_before: int) -> dict[str, int]:
    queue_result = queue_due_event_reminders(hours_before=hours_before)
    delivery_result = process_event_email_queue(limit=500)
    return {
        "matched_events": int(queue_result.get("matched_events", 0)),
        "sent": int(delivery_result.get("sent", 0)),
        "failed": int(delivery_result.get("failed", 0)),
    }
