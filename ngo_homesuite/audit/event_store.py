from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from collections import defaultdict

import json
from sqlalchemy import func

from ngo_homesuite.models.core import db
from ngo_homesuite.persistence.models.workflow_tables import WorkflowEventRecord
from ngo_homesuite.persistence.base_repository import enforce_write_gate
from ngo_homesuite.shared_kernel import redact_payload


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
    version: int = 1
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


class DbEventStore:
    """DB-backed append-only event store for workflow runtime events.
    
    All writes must go through WriteGate. This is enforced at method entry.
    """

    @staticmethod
    def _assert_org_id(org_id: str) -> None:
        if not str(org_id).strip():
            raise PermissionError("Tenant isolation requires non-empty org_id")

    @enforce_write_gate
    def append(self, event: AuditEvent) -> None:
        self.append_batch([event])

    @enforce_write_gate
    def append_batch(self, events: list[AuditEvent], *, tx: Any | None = None) -> None:
        self._validate_batch(events)
        records = [
            WorkflowEventRecord(
                event_id=event.event_id,
                org_id=event.org_id,
                event_type=event.event_type,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                actor_id=event.actor_id,
                version=event.version,
                payload_json=json.dumps(redact_payload(event.payload), sort_keys=True),
                occurred_at=event.occurred_at,
            )
            for event in events
        ]
        db.session.add_all(records)
        if tx is None:
            db.session.commit()

    def _validate_batch(self, events: list[AuditEvent]) -> None:
        if not events:
            return

        event_ids: list[str] = []
        versions_by_aggregate: dict[tuple[str, str], list[int]] = defaultdict(list)
        idempotency_keys: list[tuple[str, str, str, str]] = []

        for event in events:
            self._assert_org_id(event.org_id)
            event_ids.append(event.event_id)

            version = int(event.version or 0)
            if version < 1:
                raise ValueError(f"Event version must be >= 1 for event_id={event.event_id}")
            versions_by_aggregate[(event.org_id, event.aggregate_id)].append(version)

            payload = event.payload if isinstance(event.payload, dict) else {}
            key = payload.get("_idempotency_key")
            if isinstance(key, str) and key.strip():
                idempotency_keys.append((event.org_id, event.aggregate_id, event.event_type, key.strip()))

        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Duplicate event_id in append_batch payload")

        existing_ids = {
            row[0]
            for row in db.session.query(WorkflowEventRecord.event_id)
            .filter(WorkflowEventRecord.event_id.in_(event_ids))
            .all()
        }
        if existing_ids:
            raise ValueError(f"Duplicate event_id already exists: {sorted(existing_ids)}")

        for (org_id, aggregate_id), versions in versions_by_aggregate.items():
            max_version, count_events = (
                db.session.query(
                    func.max(WorkflowEventRecord.version),
                    func.count(WorkflowEventRecord.event_id),
                )
                .filter_by(org_id=org_id, aggregate_id=aggregate_id)
                .one()
            )
            baseline_version = max(int(max_version or 0), int(count_events or 0))
            expected = list(range(baseline_version + 1, baseline_version + 1 + len(versions)))
            if versions != expected:
                raise ValueError(
                    f"Version sequence violation for aggregate={aggregate_id}: expected {expected}, got {versions}"
                )

        for org_id, aggregate_id, event_type, key in idempotency_keys:
            existing = (
                WorkflowEventRecord.query.filter_by(
                    org_id=org_id,
                    aggregate_id=aggregate_id,
                    event_type=event_type,
                )
                .order_by(WorkflowEventRecord.occurred_at.asc())
                .all()
            )
            for record in existing:
                payload = json.loads(record.payload_json or "{}")
                if payload.get("_idempotency_key") == key:
                    raise ValueError(
                        f"Duplicate idempotency key for aggregate={aggregate_id}, event_type={event_type}, key={key}"
                    )

    def list_events(
        self,
        *,
        org_id: str | None = None,
        aggregate_id: str | None = None,
        allow_cross_tenant: bool = False,
    ) -> list[AuditEvent]:
        if org_id is None and not allow_cross_tenant:
            raise PermissionError("Unscoped event reads require allow_cross_tenant=True")
        query = WorkflowEventRecord.query
        if org_id is not None:
            self._assert_org_id(org_id)
            query = query.filter_by(org_id=org_id)
        if aggregate_id is not None:
            query = query.filter_by(aggregate_id=aggregate_id)

        records = query.order_by(WorkflowEventRecord.occurred_at.asc()).all()
        return [
            AuditEvent(
                event_id=record.event_id,
                org_id=record.org_id,
                event_type=record.event_type,
                aggregate_type=record.aggregate_type,
                aggregate_id=record.aggregate_id,
                actor_id=record.actor_id,
                payload=json.loads(record.payload_json or "{}"),
                version=int(getattr(record, "version", 1) or 1),
                occurred_at=record.occurred_at,
            )
            for record in records
        ]


def verify_workflow_event_immutability_guards(conn: Any) -> dict[str, Any]:
    """Verify DB-level append-only trigger guards for workflow events.

    This check is SQLite-focused and ensures expected trigger names exist.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='workflow_events_v2'"
    ).fetchall()
    trigger_names = {str(row[0]) for row in rows}
    expected = {
        "trg_workflow_events_v2_no_update",
        "trg_workflow_events_v2_no_delete",
    }
    missing = sorted(expected - trigger_names)
    return {
        "ok": not missing,
        "missing": missing,
        "present": sorted(trigger_names),
    }
