from __future__ import annotations

from ngo_homesuite.workflow_engine import WorkflowInstance


class WorkflowRepository:
    """In-memory workflow repository used until DB-backed projections are enabled."""

    def __init__(self) -> None:
        self._instances: dict[str, WorkflowInstance] = {}

    def save(self, instance: WorkflowInstance) -> WorkflowInstance:
        self._instances[instance.instance_id] = instance
        return instance

    def get(self, instance_id: str) -> WorkflowInstance | None:
        return self._instances.get(instance_id)

    def list_for_org(self, org_id: str) -> list[WorkflowInstance]:
        return [w for w in self._instances.values() if w.org_id == org_id]
