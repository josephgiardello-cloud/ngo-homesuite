from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class StoredEvent:
    event_id: str
    org_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    actor_id: str
    payload: dict = field(default_factory=dict)
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
