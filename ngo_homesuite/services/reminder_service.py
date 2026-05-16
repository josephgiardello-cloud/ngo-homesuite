"""Reminder Service — dispatches task reminders via email/SMS.

Supports scheduling reminders at configurable intervals before/after due dates.
Tracks all reminder history for audit and retry logic.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from werkzeug.exceptions import NotFound

from ngo_homesuite.models.core import Task, TaskReminder, User, db
from ngo_homesuite.utils.sms_service import send_sms

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# CRUD for reminder history
# ---------------------------------------------------------------------------

def create_reminder_record(
    task_id: int,
    organization_id: int,
    sent_to_user_id: int,
    *,
    channel: str = "email",
    recipient_email: Optional[str] = None,
    recipient_phone: Optional[str] = None,
    reminder_type: str = "upcoming",
    delivery_status: str = "pending",
    delivery_error: Optional[str] = None,
) -> TaskReminder:
    """Create an immutable reminder history record."""
    reminder = TaskReminder(
        task_id=task_id,
        organization_id=organization_id,
        sent_to_user_id=sent_to_user_id,
        channel=channel,
        recipient_email=recipient_email,
        recipient_phone=recipient_phone,
        reminder_type=reminder_type,
        delivery_status=delivery_status,
        delivery_error=delivery_error,
    )
    db.session.add(reminder)
    db.session.commit()
    return reminder


def list_reminders(
    organization_id: int,
    *,
    task_id: Optional[int] = None,
    user_id: Optional[int] = None,
    delivery_status: Optional[str] = None,
) -> List[TaskReminder]:
    """List reminder history with optional filters."""
    stmt = select(TaskReminder).where(TaskReminder.organization_id == organization_id)
    if task_id is not None:
        stmt = stmt.where(TaskReminder.task_id == task_id)
    if user_id is not None:
        stmt = stmt.where(TaskReminder.sent_to_user_id == user_id)
    if delivery_status is not None:
        stmt = stmt.where(TaskReminder.delivery_status == delivery_status)
    stmt = stmt.order_by(TaskReminder.sent_at.desc())
    return list(db.session.scalars(stmt))


# ---------------------------------------------------------------------------
# Reminder dispatch logic
# ---------------------------------------------------------------------------

def _get_task_or_404(task_id: int, organization_id: int) -> Task:
    """Fetch task with org scope check."""
    task = db.session.scalars(
        select(Task).where(Task.id == task_id, Task.organization_id == organization_id).limit(1)
    ).first()
    if task is None:
        raise NotFound()
    return task


def _send_task_reminder_email(
    *,
    to_email: str,
    user_name: str,
    task_title: str,
    task_description: Optional[str],
    due_date_iso: str,
    reminder_type: str,
) -> bool:
    """Send task reminder email to assignee."""
    import os

    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("EMAIL_FROM")

    if not smtp_host or not from_email or not to_email:
        return False

    # Build subject based on reminder type
    if reminder_type == "overdue":
        subject = f"OVERDUE: {task_title[:60]}"
    elif reminder_type == "escalation":
        subject = f"ESCALATED: {task_title[:60]} (seriously overdue)"
    else:
        subject = f"Reminder: {task_title[:60]}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(
        f"Hi {user_name},\n\n"
        f"This is a reminder about your task: {task_title}\n"
        f"Due: {due_date_iso}\n"
        f"Type: {reminder_type}\n"
        f"\n"
        f"{task_description or 'No description provided.'}\n"
        f"\nPlease log in to HomeSuite to update task status.\n"
    )

    try:
        with smtplib.SMTP(smtp_host, int(os.getenv("SMTP_PORT", "587"))) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Task reminder email failed for %s: %s", to_email, exc)
        return False


def dispatch_task_reminder(
    task_id: int,
    organization_id: int,
    *,
    reminder_type: str = "upcoming",
) -> Dict[str, Any]:
    """Dispatch a single task reminder to its assignee.

    Returns dict with keys:
        - sent: True if delivery succeeded
        - channel: which channel was used
        - delivery_status: 'sent' or 'failed'
        - error: error message if failed
    """
    task = _get_task_or_404(task_id, organization_id)

    if not task.assigned_to_id:
        return {
            "sent": False,
            "channel": "none",
            "delivery_status": "skipped",
            "error": "Task has no assignee",
        }

    user = db.session.get(User, task.assigned_to_id)
    if not user or not user.email:
        return {
            "sent": False,
            "channel": "none",
            "delivery_status": "skipped",
            "error": "Assignee has no email address",
        }

    # Determine channel preference
    channel = task.reminder_channel or "email"
    if channel == "none":
        return {
            "sent": False,
            "channel": "none",
            "delivery_status": "skipped",
            "error": "Reminders disabled for this task",
        }

    # Try email first (default)
    if channel in {"auto", "email"}:
        ok = _send_task_reminder_email(
            to_email=user.email,
            user_name=user.username,
            task_title=task.title,
            task_description=task.description,
            due_date_iso=task.due_date.isoformat() if task.due_date else "unknown",
            reminder_type=reminder_type,
        )
        if ok:
            # Update task reminder tracking
            task.last_reminder_sent_at = _utcnow()
            task.reminder_sent_count = (task.reminder_sent_count or 0) + 1
            task.last_reminder_error = None
            db.session.commit()

            # Record in history
            create_reminder_record(
                task_id=task_id,
                organization_id=organization_id,
                sent_to_user_id=user.id,
                channel="email",
                recipient_email=user.email,
                reminder_type=reminder_type,
                delivery_status="sent",
            )
            return {
                "sent": True,
                "channel": "email",
                "delivery_status": "sent",
                "error": None,
            }

    # Fall back to SMS if email failed and SMS enabled
    if channel in {"auto", "sms"} and user.phone:
        body = f"Reminder: {task.title} is due {task.due_date.isoformat() if task.due_date else 'soon'}. Check HomeSuite."
        ok = send_sms(user.phone, body)
        if ok:
            task.last_reminder_sent_at = _utcnow()
            task.reminder_sent_count = (task.reminder_sent_count or 0) + 1
            task.last_reminder_error = None
            db.session.commit()

            create_reminder_record(
                task_id=task_id,
                organization_id=organization_id,
                sent_to_user_id=user.id,
                channel="sms",
                recipient_phone=user.phone,
                reminder_type=reminder_type,
                delivery_status="sent",
            )
            return {
                "sent": True,
                "channel": "sms",
                "delivery_status": "sent",
                "error": None,
            }

    # All delivery methods failed
    error_msg = "No deliverable channel (email/SMS)"
    task.last_reminder_error = error_msg
    db.session.commit()

    create_reminder_record(
        task_id=task_id,
        organization_id=organization_id,
        sent_to_user_id=user.id,
        channel=channel,
        recipient_email=user.email,
        recipient_phone=user.phone,
        reminder_type=reminder_type,
        delivery_status="failed",
        delivery_error=error_msg,
    )

    return {
        "sent": False,
        "channel": channel,
        "delivery_status": "failed",
        "error": error_msg,
    }


# ---------------------------------------------------------------------------
# Bulk dispatch for scheduled reminders
# ---------------------------------------------------------------------------

def dispatch_upcoming_task_reminders(
    organization_id: int,
    *,
    hours_before_due: float = 24.0,
) -> Dict[str, Any]:
    """Dispatch reminders for tasks due in the next N hours.

    Only sends if no reminder was sent in the last 12 hours.
    """
    now = _utcnow()
    upcoming_threshold = now + timedelta(hours=hours_before_due)
    last_reminded_within = now - timedelta(hours=12)

    stmt = select(Task).where(
        Task.organization_id == organization_id,
        Task.status.in_(["open", "in_progress"]),
        Task.due_date > now,
        Task.due_date <= upcoming_threshold,
        Task.assigned_to_id.is_not(None),
    )

    # Exclude tasks that were reminded recently
    stmt = stmt.where(
        (Task.last_reminder_sent_at.is_(None)) | (Task.last_reminder_sent_at < last_reminded_within)
    )

    tasks = list(db.session.scalars(stmt))

    sent = 0
    failed = 0
    for task in tasks:
        result = dispatch_task_reminder(task.id, organization_id, reminder_type="upcoming")
        if result["sent"]:
            sent += 1
        else:
            failed += 1

    return {
        "sent": sent,
        "failed": failed,
        "total_processed": len(tasks),
        "hours_before_due": hours_before_due,
    }


def dispatch_overdue_task_reminders(organization_id: int) -> Dict[str, Any]:
    """Dispatch reminders for tasks past their due date."""
    now = _utcnow()
    last_reminded_within = now - timedelta(hours=24)

    stmt = select(Task).where(
        Task.organization_id == organization_id,
        Task.status.in_(["open", "in_progress"]),
        Task.due_date <= now,
        Task.assigned_to_id.is_not(None),
    )

    # Exclude tasks reminded recently
    stmt = stmt.where(
        (Task.last_reminder_sent_at.is_(None)) | (Task.last_reminder_sent_at < last_reminded_within)
    )

    tasks = list(db.session.scalars(stmt))

    sent = 0
    failed = 0
    for task in tasks:
        # Determine if this is escalation (more than 2 days overdue)
        days_overdue = (now - task.due_date).days
        reminder_type = "escalation" if days_overdue > 2 else "overdue"
        result = dispatch_task_reminder(task.id, organization_id, reminder_type=reminder_type)
        if result["sent"]:
            sent += 1
        else:
            failed += 1

    return {
        "sent": sent,
        "failed": failed,
        "total_processed": len(tasks),
    }


def recommend_task_reminders(
    organization_id: int,
    *,
    limit: int = 25,
    task_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Return tasks that should receive reminders now (upcoming/overdue/escalation)."""
    now = _utcnow()
    upcoming_threshold = now + timedelta(hours=72)
    last_reminded_within = now - timedelta(hours=12)

    stmt = select(Task).where(
        Task.organization_id == organization_id,
        Task.status.in_(["open", "in_progress"]),
        Task.assigned_to_id.is_not(None),
        Task.due_date.is_not(None),
        Task.due_date <= upcoming_threshold,
        Task.reminder_channel != "none",
    )
    if task_ids:
        stmt = stmt.where(Task.id.in_(task_ids))

    stmt = stmt.where(
        (Task.last_reminder_sent_at.is_(None)) | (Task.last_reminder_sent_at < last_reminded_within)
    ).order_by(Task.due_date.asc())

    tasks = list(db.session.scalars(stmt.limit(max(1, min(limit, 200)))))

    payload: List[Dict[str, Any]] = []
    for task in tasks:
        if task.due_date is None:
            continue

        delta = task.due_date - now
        hours_until_due = int(delta.total_seconds() // 3600)
        days_overdue = (now - task.due_date).days

        if task.due_date < now:
            reminder_type = "escalation" if days_overdue > 2 else "overdue"
        else:
            reminder_type = "upcoming"

        payload.append(
            {
                "task_id": task.id,
                "title": task.title,
                "status": task.status,
                "priority": task.priority,
                "due_date": task.due_date.isoformat(),
                "assigned_to_id": task.assigned_to_id,
                "reminder_channel": task.reminder_channel,
                "reminder_type": reminder_type,
                "hours_until_due": hours_until_due,
                "days_overdue": max(0, days_overdue),
            }
        )
    return payload


def reminder_summary(
    organization_id: int,
) -> Dict[str, Any]:
    """Get summary of tasks needing reminders."""
    now = _utcnow()

    # Overdue
    overdue = list(
        db.session.scalars(
            select(Task).where(
                Task.organization_id == organization_id,
                Task.status.in_(["open", "in_progress"]),
                Task.due_date <= now,
            )
        )
    )

    # Upcoming (due within 24h)
    upcoming_threshold = now + timedelta(hours=24)
    upcoming = list(
        db.session.scalars(
            select(Task).where(
                Task.organization_id == organization_id,
                Task.status.in_(["open", "in_progress"]),
                Task.due_date > now,
                Task.due_date <= upcoming_threshold,
            )
        )
    )

    return {
        "overdue_count": len(overdue),
        "upcoming_count": len(upcoming),
        "total_open": len(overdue) + len(upcoming),
    }
