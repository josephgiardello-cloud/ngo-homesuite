from __future__ import annotations

import json

from ngo_homesuite.models.core import db
from ngo_homesuite.persistence.models.workflow_tables import WorkflowInstanceRecord
from ngo_homesuite.shared_kernel import redact_payload
from ngo_homesuite.workflow_engine import WorkflowInstance
from ngo_homesuite.workflow_engine.instance import WorkflowStatus


class WorkflowRepository:
    """DB-backed workflow projection repository."""

    @staticmethod
    def _assert_org_id(org_id: str) -> None:
        if not str(org_id).strip():
            raise PermissionError("Tenant isolation requires non-empty org_id")

    @staticmethod
    def _to_record_history(history: list[dict]) -> str:
        redacted = []
        for item in history:
            row = dict(item)
            row["payload"] = redact_payload(row.get("payload", {}))
            redacted.append(row)
        return json.dumps(redacted, sort_keys=True)

    @staticmethod
    def _from_record(record: WorkflowInstanceRecord) -> WorkflowInstance:
        return WorkflowInstance(
            instance_id=record.instance_id,
            org_id=record.org_id,
            workflow_type=record.workflow_type,
            current_step=record.current_step,
            status=WorkflowStatus(record.status),
            history=json.loads(record.history_json or "[]"),
            created_at=record.created_at.isoformat() if record.created_at else "",
        )

    def save(self, instance: WorkflowInstance) -> WorkflowInstance:
        self._assert_org_id(instance.org_id)
        record = WorkflowInstanceRecord.query.filter_by(instance_id=instance.instance_id).first()
        history_json = self._to_record_history(instance.history)
        if record is None:
            record = WorkflowInstanceRecord(
                instance_id=instance.instance_id,
                org_id=instance.org_id,
                workflow_type=instance.workflow_type,
                current_step=instance.current_step,
                status=str(instance.status),
                history_json=history_json,
            )
            db.session.add(record)
        else:
            record.current_step = instance.current_step
            record.status = str(instance.status)
            record.history_json = history_json
        db.session.commit()
        return self._from_record(record)

    def get(
        self,
        instance_id: str,
        *,
        org_id: str | None = None,
        allow_cross_tenant: bool = False,
    ) -> WorkflowInstance | None:
        if org_id is None and not allow_cross_tenant:
            raise PermissionError("Unscoped instance reads require allow_cross_tenant=True")
        query = WorkflowInstanceRecord.query.filter_by(instance_id=instance_id)
        if org_id is not None:
            self._assert_org_id(org_id)
            query = query.filter_by(org_id=org_id)
        record = query.first()
        if record is None:
            return None
        return self._from_record(record)

    def list_for_org(self, org_id: str) -> list[WorkflowInstance]:
        self._assert_org_id(org_id)
        records = (
            WorkflowInstanceRecord.query.filter_by(org_id=org_id)
            .order_by(WorkflowInstanceRecord.created_at.asc())
            .all()
        )
        return [self._from_record(record) for record in records]
