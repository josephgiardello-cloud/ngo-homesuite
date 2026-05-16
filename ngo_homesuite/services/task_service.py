"""Task Management service — Moves Management for nonprofits.

Supports task creation, assignment, completion, and automated task generation
from donation/grant/workflow triggers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from werkzeug.exceptions import NotFound

from ngo_homesuite.models.core import Donation, Grant, Task, db

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_task(
    organization_id: int,
    title: str,
    *,
    assigned_to_id: Optional[int] = None,
    donor_id: Optional[int] = None,
    grant_id: Optional[int] = None,
    donation_id: Optional[int] = None,
    project_id: Optional[int] = None,
    task_type: str = "general",
    priority: str = "medium",
    due_date: Optional[datetime] = None,
    description: Optional[str] = None,
    notes: Optional[str] = None,
) -> Task:
    task = Task(
        organization_id=organization_id,
        title=title,
        assigned_to_id=assigned_to_id,
        donor_id=donor_id,
        grant_id=grant_id,
        donation_id=donation_id,
        project_id=project_id,
        task_type=task_type,
        priority=priority,
        due_date=due_date,
        description=description,
        notes=notes,
    )
    db.session.add(task)
    db.session.commit()
    return task


def get_task(task_id: int, organization_id: int) -> Optional[Task]:
    return db.session.scalars(
        select(Task).where(Task.id == task_id, Task.organization_id == organization_id).limit(1)
    ).first()


def list_tasks(
    organization_id: int,
    *,
    donor_id: Optional[int] = None,
    grant_id: Optional[int] = None,
    project_id: Optional[int] = None,
    donation_id: Optional[int] = None,
    assigned_to_id: Optional[int] = None,
    task_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    overdue_only: bool = False,
    due_within_days: Optional[int] = None,
) -> List[Task]:
    stmt = select(Task).where(Task.organization_id == organization_id)
    if donor_id is not None:
        stmt = stmt.where(Task.donor_id == donor_id)
    if grant_id is not None:
        stmt = stmt.where(Task.grant_id == grant_id)
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    if donation_id is not None:
        stmt = stmt.where(Task.donation_id == donation_id)
    if assigned_to_id is not None:
        stmt = stmt.where(Task.assigned_to_id == assigned_to_id)
    if task_type:
        stmt = stmt.where(Task.task_type == task_type)
    if status:
        stmt = stmt.where(Task.status == status)
    if priority:
        stmt = stmt.where(Task.priority == priority)
    if overdue_only:
        stmt = stmt.where(Task.due_date <= _utcnow(), Task.status.in_(["open", "in_progress"]))
    if due_within_days is not None:
        now = _utcnow()
        stmt = stmt.where(Task.due_date.is_not(None), Task.due_date <= now + timedelta(days=max(0, due_within_days)))
    stmt = stmt.order_by(Task.due_date.asc().nullslast(), Task.created_at.desc())
    return list(db.session.scalars(stmt))


def complete_task(task_id: int, organization_id: int, *, notes: Optional[str] = None) -> Task:
    task = db.session.scalars(
        select(Task).where(Task.id == task_id, Task.organization_id == organization_id).limit(1)
    ).first()
    if task is None:
        raise NotFound()
    task.status = "done"
    task.completed_at = _utcnow()
    if notes:
        task.notes = (task.notes or "") + f"\n[Completion note] {notes}"
    db.session.commit()
    return task


def update_task(task_id: int, organization_id: int, **fields) -> Task:
    task = db.session.scalars(
        select(Task).where(Task.id == task_id, Task.organization_id == organization_id).limit(1)
    ).first()
    if task is None:
        raise NotFound()
    allowed = {
        "title", "description", "task_type", "priority", "status",
        "due_date", "assigned_to_id", "notes",
    }
    for k, v in fields.items():
        if k in allowed:
            setattr(task, k, v)
    if fields.get("status") == "done" and not task.completed_at:
        task.completed_at = _utcnow()
    db.session.commit()
    return task


def delete_task(task_id: int, organization_id: int) -> None:
    task = db.session.scalars(
        select(Task).where(Task.id == task_id, Task.organization_id == organization_id).limit(1)
    ).first()
    if task is None:
        raise NotFound()
    db.session.delete(task)
    db.session.commit()


# ---------------------------------------------------------------------------
# Automated task generation triggers
# ---------------------------------------------------------------------------

def auto_tasks_for_major_donation(
    donation_id: int,
    organization_id: int,
    major_gift_threshold: float = 500.0,
    assigned_to_id: Optional[int] = None,
) -> List[Task]:
    """When a major gift is recorded, create follow-up tasks automatically."""
    donation = db.session.scalars(
        select(Donation).where(Donation.id == donation_id, Donation.organization_id == organization_id).limit(1)
    ).first()
    if not donation or donation.amount < major_gift_threshold:
        return []

    created = []
    now = _utcnow()

    # Immediate thank-you task
    created.append(create_task(
        organization_id=organization_id,
        title=f"Send personal thank-you to {donation.donor_name} for ${donation.amount:,.0f} gift",
        task_type="email",
        priority="high",
        donor_id=donation.donor_id,
        donation_id=donation_id,
        assigned_to_id=assigned_to_id,
        due_date=now + timedelta(days=2),
        description="Major gift acknowledgement — send within 48 hours.",
    ))

    # 90-day follow-up / next ask
    created.append(create_task(
        organization_id=organization_id,
        title=f"90-day major donor follow-up — {donation.donor_name}",
        task_type="call",
        priority="medium",
        donor_id=donation.donor_id,
        assigned_to_id=assigned_to_id,
        due_date=now + timedelta(days=90),
        description=(
            f"Cultivate relationship after ${donation.amount:,.0f} gift. "
            "Review engagement score and suggest next ask amount."
        ),
    ))
    return created


def auto_tasks_for_grant_deadline(
    grant_id: int,
    organization_id: int,
    assigned_to_id: Optional[int] = None,
) -> List[Task]:
    """Create reminder tasks before a grant deadline."""
    grant = db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()
    if not grant or not grant.application_deadline:
        return []

    import datetime as dt
    deadline = datetime.combine(grant.application_deadline, datetime.min.time())
    now = _utcnow()
    created = []

    if (deadline - now).days > 14:
        created.append(create_task(
            organization_id=organization_id,
            title=f"Start grant application: {grant.title[:60]}",
            task_type="general",
            priority="high",
            grant_id=grant_id,
            assigned_to_id=assigned_to_id,
            due_date=deadline - timedelta(days=14),
            description=f"Application deadline: {grant.application_deadline}. Funder: {grant.funder_name}.",
        ))

    created.append(create_task(
        organization_id=organization_id,
        title=f"Final review before submission: {grant.title[:60]}",
        task_type="general",
        priority="urgent",
        grant_id=grant_id,
        assigned_to_id=assigned_to_id,
        due_date=deadline - timedelta(days=3),
        description=f"3-day check before deadline {grant.application_deadline}.",
    ))
    return created


def overdue_task_summary(organization_id: int) -> dict:
    overdue = list_tasks(organization_id, overdue_only=True)
    by_priority = {"urgent": 0, "high": 0, "medium": 0, "low": 0}
    for t in overdue:
        by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
    return {"total_overdue": len(overdue), "by_priority": by_priority}


def task_board_snapshot(
    organization_id: int,
    *,
    donor_id: Optional[int] = None,
    grant_id: Optional[int] = None,
    project_id: Optional[int] = None,
    donation_id: Optional[int] = None,
    assigned_to_id: Optional[int] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> dict:
    """Return a task board payload with summary metrics for operational triage."""
    tasks = list_tasks(
        organization_id,
        donor_id=donor_id,
        grant_id=grant_id,
        project_id=project_id,
        donation_id=donation_id,
        assigned_to_id=assigned_to_id,
        status=status,
        priority=priority,
    )

    now = _utcnow()
    by_status = {"open": 0, "in_progress": 0, "done": 0, "cancelled": 0}
    by_priority = {"urgent": 0, "high": 0, "medium": 0, "low": 0}
    due_buckets = {"overdue": 0, "today": 0, "next_7_days": 0, "later": 0, "no_due_date": 0}
    linked = {"donor": 0, "grant": 0, "project": 0, "donation": 0}

    for task in tasks:
        by_status[task.status] = by_status.get(task.status, 0) + 1
        by_priority[task.priority] = by_priority.get(task.priority, 0) + 1
        if task.donor_id:
            linked["donor"] += 1
        if task.grant_id:
            linked["grant"] += 1
        if task.project_id:
            linked["project"] += 1
        if task.donation_id:
            linked["donation"] += 1

        if task.due_date is None:
            due_buckets["no_due_date"] += 1
        elif task.status in {"open", "in_progress"} and task.due_date < now:
            due_buckets["overdue"] += 1
        elif task.due_date.date() == now.date():
            due_buckets["today"] += 1
        elif task.due_date <= now + timedelta(days=7):
            due_buckets["next_7_days"] += 1
        else:
            due_buckets["later"] += 1

    return {
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "open_total": by_status.get("open", 0) + by_status.get("in_progress", 0),
            "by_status": by_status,
            "by_priority": by_priority,
            "due_buckets": due_buckets,
            "linked_entities": linked,
        },
    }
