from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    """Append-only event shared by workflow/runtime/domain operations."""

    event_id: str
    org_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class InMemoryEventStore:
    """Simple append-only event store used as the V2 default runtime store."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def list_events(self, *, org_id: str | None = None, aggregate_id: str | None = None) -> list[AuditEvent]:
        events = self._events
        if org_id is not None:
            events = [e for e in events if e.org_id == org_id]
        if aggregate_id is not None:
            events = [e for e in events if e.aggregate_id == aggregate_id]
        return list(events)
