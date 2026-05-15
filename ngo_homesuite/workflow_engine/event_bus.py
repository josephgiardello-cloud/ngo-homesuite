from __future__ import annotations

from ngo_homesuite.audit import AuditEvent, InMemoryEventStore


class EventEmitter:
    """Event emitter facade used by workflow engine and integrations."""

    def __init__(self, event_store: InMemoryEventStore) -> None:
        self._event_store = event_store

    def emit(self, event: AuditEvent) -> None:
        self._event_store.append(event)
