from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class WorkflowTrace:
    workflow_instance_id: str
    org_id: str
    steps: list[dict] = field(default_factory=list)


class WorkflowTracer:
    """In-memory trace sink; can be swapped for OTEL exporters later."""

    def __init__(self) -> None:
        self._traces: dict[str, WorkflowTrace] = {}

    def record(self, *, workflow_instance_id: str, org_id: str, step: str, event_type: str) -> None:
        trace = self._traces.setdefault(
            workflow_instance_id,
            WorkflowTrace(workflow_instance_id=workflow_instance_id, org_id=org_id),
        )
        trace.steps.append(
            {
                "step": step,
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def get(self, workflow_instance_id: str) -> WorkflowTrace | None:
        return self._traces.get(workflow_instance_id)
