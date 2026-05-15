from __future__ import annotations

import hashlib
import json

from ngo_homesuite.models.core import db
from ngo_homesuite.persistence.interfaces import UnitOfWorkPort
from ngo_homesuite.persistence.models.workflow_tables import WorkflowDefinitionRecord
from ngo_homesuite.workflow_engine import StepNode, TransitionRule, WorkflowDefinition


class WorkflowDefinitionRepository:
    """DB-backed workflow definition repository with monotonic versioning.

    Workflow definitions are intentionally global-scope metadata. Callers must explicitly
    acknowledge this by passing allow_global_scope=True.
    """

    @staticmethod
    def _serialize(definition: WorkflowDefinition) -> tuple[str, str]:
        steps = {
            name: {"name": node.name, "terminal": bool(node.terminal)}
            for name, node in sorted(definition.steps.items(), key=lambda item: item[0])
        }
        transitions = [
            {
                "from_step": t.from_step,
                "event_type": t.event_type,
                "to_step": t.to_step,
            }
            for t in definition.transitions
        ]
        return json.dumps(steps, sort_keys=True), json.dumps(transitions, sort_keys=True)

    @staticmethod
    def _hash(definition: WorkflowDefinition) -> str:
        steps_json, transitions_json = WorkflowDefinitionRepository._serialize(definition)
        body = f"{definition.workflow_type}|{definition.initial_step}|{steps_json}|{transitions_json}"
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_definition(record: WorkflowDefinitionRecord) -> WorkflowDefinition:
        raw_steps = json.loads(record.steps_json)
        steps = {
            key: StepNode(name=value["name"], terminal=bool(value.get("terminal", False)))
            for key, value in raw_steps.items()
        }
        raw_transitions = json.loads(record.transitions_json)
        transitions = [
            TransitionRule(
                from_step=item["from_step"],
                event_type=item["event_type"],
                to_step=item["to_step"],
            )
            for item in raw_transitions
        ]
        return WorkflowDefinition(
            workflow_type=record.workflow_type,
            initial_step=record.initial_step,
            steps=steps,
            transitions=transitions,
        )

    def ensure_definition(
        self,
        definition: WorkflowDefinition,
        *,
        allow_global_scope: bool = False,
        uow: UnitOfWorkPort | None = None,
    ) -> WorkflowDefinitionRecord:
        if not allow_global_scope:
            raise PermissionError("Global-scope definition access requires allow_global_scope=True")
        definition_hash = self._hash(definition)
        latest = (
            WorkflowDefinitionRecord.query.filter_by(workflow_type=definition.workflow_type)
            .order_by(WorkflowDefinitionRecord.version.desc())
            .first()
        )
        if latest is not None and latest.definition_hash == definition_hash:
            if not latest.is_active:
                latest.is_active = True
                if uow is None:
                    db.session.commit()
            return latest

        next_version = 1 if latest is None else latest.version + 1
        WorkflowDefinitionRecord.query.filter_by(workflow_type=definition.workflow_type).update({"is_active": False})
        steps_json, transitions_json = self._serialize(definition)
        record = WorkflowDefinitionRecord(
            workflow_type=definition.workflow_type,
            version=next_version,
            definition_hash=definition_hash,
            is_active=True,
            initial_step=definition.initial_step,
            steps_json=steps_json,
            transitions_json=transitions_json,
        )
        db.session.add(record)
        if uow is None:
            db.session.commit()
        return record

    def list_active_definitions(self, *, allow_global_scope: bool = False) -> dict[str, WorkflowDefinition]:
        if not allow_global_scope:
            raise PermissionError("Global-scope definition access requires allow_global_scope=True")
        records = WorkflowDefinitionRecord.query.filter_by(is_active=True).all()
        return {record.workflow_type: self._to_definition(record) for record in records}
