from __future__ import annotations

from ngo_homesuite.workflow_engine import WorkflowInstance


def summarize_workflow_instances(instances: list[WorkflowInstance]) -> dict:
    summary: dict[str, int] = {}
    for instance in instances:
        key = f"{instance.workflow_type}:{instance.status}"
        summary[key] = summary.get(key, 0) + 1
    return summary
