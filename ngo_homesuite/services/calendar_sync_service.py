from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select

from ngo_homesuite.models.core import Task, db


class CalendarProvider(Protocol):
    def upsert_event(self, *, event_key: str, title: str, due_date: datetime, metadata: dict[str, Any]) -> None:
        ...


@dataclass
class InMemoryCalendarProvider:
    events: dict[str, dict[str, Any]] = field(default_factory=dict)

    def upsert_event(self, *, event_key: str, title: str, due_date: datetime, metadata: dict[str, Any]) -> None:
        self.events[event_key] = {
            "title": title,
            "due_date": due_date,
            "metadata": dict(metadata),
        }



def sync_task_deadlines(
    organization_id: int,
    provider: CalendarProvider,
    *,
    statuses: tuple[str, ...] = ("open", "in_progress"),
) -> dict[str, int]:
    tasks = list(
        db.session.scalars(
            select(Task)
            .where(
                Task.organization_id == organization_id,
                Task.status.in_(list(statuses)),
                Task.due_date.is_not(None),
            )
            .order_by(Task.due_date.asc())
        )
    )

    synced = 0
    skipped = 0
    for task in tasks:
        if task.due_date is None:
            skipped += 1
            continue
        provider.upsert_event(
            event_key=f"task:{task.id}",
            title=task.title,
            due_date=task.due_date,
            metadata={
                "organization_id": task.organization_id,
                "task_id": task.id,
                "status": task.status,
                "priority": task.priority,
            },
        )
        synced += 1

    return {"synced": synced, "skipped": skipped}
