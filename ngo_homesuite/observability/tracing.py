from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ngo_homesuite.observability.context import get_request_id


@dataclass
class WorkflowTrace:
    workflow_instance_id: str
    org_id: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    steps: list[dict] = field(default_factory=list)


class WorkflowTracer:
    """In-memory trace sink; can be swapped for OTEL exporters later."""

    def __init__(self) -> None:
        self._traces: dict[str, WorkflowTrace] = {}

    def record(
        self,
        *,
        workflow_instance_id: str,
        org_id: str,
        step: str,
        event_type: str,
        duration_ms: float | None = None,
    ) -> None:
        trace = self._traces.setdefault(
            workflow_instance_id,
            WorkflowTrace(workflow_instance_id=workflow_instance_id, org_id=org_id),
        )
        event = {
            "step": step,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": get_request_id(),
        }
        if duration_ms is not None:
            event["duration_ms"] = round(float(duration_ms), 3)
        trace.steps.append(event)

    def get(self, workflow_instance_id: str) -> WorkflowTrace | None:
        return self._traces.get(workflow_instance_id)
