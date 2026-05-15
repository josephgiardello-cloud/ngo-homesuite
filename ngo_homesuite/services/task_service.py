"""Task Management service — Moves Management for nonprofits.

Supports task creation, assignment, completion, and automated task generation
from donation/grant/workflow triggers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

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
    return Task.query.filter_by(id=task_id, organization_id=organization_id).first()


def list_tasks(
    organization_id: int,
    *,
    donor_id: Optional[int] = None,
    grant_id: Optional[int] = None,
    assigned_to_id: Optional[int] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    overdue_only: bool = False,
) -> List[Task]:
    q = Task.query.filter_by(organization_id=organization_id)
    if donor_id is not None:
        q = q.filter_by(donor_id=donor_id)
    if grant_id is not None:
        q = q.filter_by(grant_id=grant_id)
    if assigned_to_id is not None:
        q = q.filter_by(assigned_to_id=assigned_to_id)
    if status:
        q = q.filter_by(status=status)
    if priority:
        q = q.filter_by(priority=priority)
    if overdue_only:
        q = q.filter(Task.due_date <= _utcnow(), Task.status.in_(["open", "in_progress"]))
    return q.order_by(Task.due_date.asc().nullslast(), Task.created_at.desc()).all()


def complete_task(task_id: int, organization_id: int, *, notes: Optional[str] = None) -> Task:
    task = Task.query.filter_by(id=task_id, organization_id=organization_id).first_or_404()
    task.status = "done"
    task.completed_at = _utcnow()
    if notes:
        task.notes = (task.notes or "") + f"\n[Completion note] {notes}"
    db.session.commit()
    return task


def update_task(task_id: int, organization_id: int, **fields) -> Task:
    task = Task.query.filter_by(id=task_id, organization_id=organization_id).first_or_404()
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
    task = Task.query.filter_by(id=task_id, organization_id=organization_id).first_or_404()
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
    donation = Donation.query.filter_by(id=donation_id, organization_id=organization_id).first()
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
    grant = Grant.query.filter_by(id=grant_id, organization_id=organization_id).first()
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
