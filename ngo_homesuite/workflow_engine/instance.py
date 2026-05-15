from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class WorkflowStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class WorkflowInstance:
    instance_id: str
    org_id: str
    workflow_type: str
    current_step: str
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    history: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def record_transition(self, *, event_type: str, from_step: str, to_step: str, payload: dict | None = None) -> None:
        self.history.append(
            {
                "event_type": event_type,
                "from_step": from_step,
                "to_step": to_step,
                "payload": payload or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
