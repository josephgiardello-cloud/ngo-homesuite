from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ngo_homesuite.audit import AuditEvent, DbEventStore
from ngo_homesuite.models.core import db
from ngo_homesuite.persistence import SqlAlchemyUnitOfWork
from ngo_homesuite.persistence.write_context import current_context, enter_write_gate, exit_write_gate
from ngo_homesuite.shared_kernel import new_id


@dataclass(frozen=True)
class Result:
    value: Any
    events: list[AuditEvent] = field(default_factory=list)


@dataclass(frozen=True)
class FailureSemantics:
    code: str
    classification: str
    retryable: bool
    http_status: int


class WriteGateExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        execution_id: str,
        action: str,
        semantics: FailureSemantics,
        cause: Exception,
    ) -> None:
        super().__init__(message)
        self.execution_id = execution_id
        self.action = action
        self.code = semantics.code
        self.classification = semantics.classification
        self.retryable = semantics.retryable
        self.http_status = semantics.http_status
        self.__cause__ = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "action": self.action,
            "code": self.code,
            "classification": self.classification,
            "retryable": self.retryable,
            "http_status": self.http_status,
        }


def _classify_exception(exc: Exception) -> FailureSemantics:
    if isinstance(exc, PermissionError):
        return FailureSemantics(code="authorization_denied", classification="security", retryable=False, http_status=403)
    if isinstance(exc, KeyError):
        return FailureSemantics(code="not_found", classification="domain", retryable=False, http_status=404)
    if isinstance(exc, ValueError):
        return FailureSemantics(code="validation_failed", classification="validation", retryable=False, http_status=400)
    if isinstance(exc, RuntimeError):
        return FailureSemantics(code="state_conflict", classification="state", retryable=False, http_status=409)
    return FailureSemantics(code="internal_error", classification="system", retryable=True, http_status=500)


class WriteGate:
    def __init__(self, *, domain_handler: Any, uow: SqlAlchemyUnitOfWork, event_store: DbEventStore) -> None:
        self.domain_handler = domain_handler
        self.uow = uow
        self.event_store = event_store

    def execute(self, command: Any) -> Result:
        execution_id = new_id("cmd")
        action = str(getattr(command, "action", "unknown"))
        payload = {
            "action": action,
            "org_id": getattr(command, "org_id", None),
            "actor_id": getattr(command, "actor_id", None),
            "instance_id": getattr(command, "instance_id", None),
            "workflow_type": getattr(command, "workflow_type", None),
            "idempotency_key": getattr(command, "idempotency_key", None),
        }

        try:
            with self.uow.transaction() as tx:
                token = enter_write_gate(
                    command_id=getattr(command, "instance_id", None) or getattr(command, "command_id", None),
                    actor_id=getattr(command, "actor_id", None),
                    org_id=getattr(command, "org_id", None),
                    metadata={"action": getattr(command, "action", None)},
                )
                try:
                    start_event = AuditEvent(
                        event_id=new_id("evt"),
                        org_id=str(getattr(command, "org_id", "") or ""),
                        event_type="workflow_command_started",
                        aggregate_type="workflow_command",
                        aggregate_id=execution_id,
                        actor_id=str(getattr(command, "actor_id", "system") or "system"),
                        payload=payload,
                        version=1,
                    )
                    result = self.domain_handler.handle(command, uow=tx)
                    outcome = {
                        **payload,
                        "result": "ok",
                    }
                    completed_event = AuditEvent(
                        event_id=new_id("evt"),
                        org_id=str(getattr(command, "org_id", "") or ""),
                        event_type="workflow_command_completed",
                        aggregate_type="workflow_command",
                        aggregate_id=execution_id,
                        actor_id=str(getattr(command, "actor_id", "system") or "system"),
                        payload=outcome,
                        version=2,
                    )
                    self.event_store.append_batch(result.events + [start_event, completed_event], tx=tx)
                    return result
                except Exception as exc:
                    semantics = _classify_exception(exc)
                    raise WriteGateExecutionError(
                        str(exc),
                        execution_id=execution_id,
                        action=action,
                        semantics=semantics,
                        cause=exc,
                    ) from exc
                finally:
                    exit_write_gate(token)
        except WriteGateExecutionError as exc:
            self._record_failed_execution(command=command, execution_error=exc, payload=payload)
            raise

    def _record_failed_execution(
        self,
        *,
        command: Any,
        execution_error: WriteGateExecutionError,
        payload: dict[str, Any],
    ) -> None:
        token = enter_write_gate(
            command_id=execution_error.execution_id,
            actor_id=getattr(command, "actor_id", None),
            org_id=getattr(command, "org_id", None),
            metadata={"action": getattr(command, "action", None), "phase": "record_failed_execution"},
        )
        try:
            failed_event = AuditEvent(
                event_id=new_id("evt"),
                org_id=str(getattr(command, "org_id", "") or ""),
                event_type="workflow_command_failed",
                aggregate_type="workflow_command",
                aggregate_id=execution_error.execution_id,
                actor_id=str(getattr(command, "actor_id", "system") or "system"),
                payload={
                    **payload,
                    "result": "error",
                    "error": {
                        "message": str(execution_error),
                        "code": execution_error.code,
                        "classification": execution_error.classification,
                        "retryable": execution_error.retryable,
                    },
                },
                version=1,
            )
            self.event_store.append(failed_event)
        except Exception:
            # Do not mask the original failure when execution auditing cannot be persisted.
            pass
        finally:
            exit_write_gate(token)


class CollectingEventEmitter:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self._events.append(event)

    def drain(self) -> list[AuditEvent]:
        events = list(self._events)
        self._events.clear()
        return events


@dataclass(frozen=True)
class WorkflowWriteCommand:
    action: str
    org_id: str
    actor_id: str
    role: str | None = None
    workflow_type: str | None = None
    instance_id: str | None = None
    event_type: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


class WorkflowWriteHandler:
    def __init__(self, *, workflow_repository: Any, state_machine: Any, workflow_definitions: dict[str, Any]) -> None:
        self.workflow_repository = workflow_repository
        self.state_machine = state_machine
        self.workflow_definitions = workflow_definitions

    def handle(self, command: WorkflowWriteCommand, *, uow: SqlAlchemyUnitOfWork) -> Result:
        if command.action == "create_workflow_instance":
            return self._create_instance(command, uow=uow)
        if command.action == "dispatch_workflow_event":
            return self._dispatch_event(command, uow=uow)
        raise ValueError(f"Unknown workflow write action: {command.action}")

    def _create_instance(self, command: WorkflowWriteCommand, *, uow: SqlAlchemyUnitOfWork) -> Result:
        if not command.workflow_type:
            raise ValueError("workflow_type is required")
        definition = self.workflow_definitions.get(command.workflow_type)
        if definition is None:
            raise KeyError(f"Unknown workflow type: {command.workflow_type}")

        from ngo_homesuite.shared_kernel import new_id
        from ngo_homesuite.workflow_engine import WorkflowInstance

        instance = WorkflowInstance(
            instance_id=new_id("wfi"),
            org_id=command.org_id,
            workflow_type=command.workflow_type,
            current_step=definition.initial_step,
        )
        saved = self.workflow_repository.save(instance, uow=uow)
        event = AuditEvent(
            event_id=new_id("evt"),
            org_id=command.org_id,
            event_type="workflow_instance_created",
            aggregate_type="workflow_instance",
            aggregate_id=saved.instance_id,
            actor_id=command.actor_id,
            payload={
                "workflow_type": command.workflow_type,
                "initial_step": definition.initial_step,
            },
            version=int(saved.version or 1),
        )
        return Result(value=saved, events=[event])

    def _dispatch_event(self, command: WorkflowWriteCommand, *, uow: SqlAlchemyUnitOfWork) -> Result:
        if not command.instance_id:
            raise ValueError("instance_id is required")
        if not command.event_type:
            raise ValueError("event_type is required")

        from ngo_homesuite.tenant import TenantContext

        tenant = TenantContext(
            org_id=command.org_id,
            user_id=command.actor_id,
            role=command.role or "case_worker",
        )
        instance = self.workflow_repository.get(command.instance_id, org_id=command.org_id)
        if instance is None:
            raise KeyError(f"Unknown workflow instance: {command.instance_id}")
        if command.idempotency_key:
            for item in instance.history:
                payload = item.get("payload") if isinstance(item, dict) else None
                if not isinstance(payload, dict):
                    continue
                if item.get("event_type") == command.event_type and payload.get("_idempotency_key") == command.idempotency_key:
                    return Result(value=(instance, True), events=[])

        definition = self.workflow_definitions[instance.workflow_type]
        payload = dict(command.payload)
        if command.idempotency_key:
            payload["_idempotency_key"] = command.idempotency_key
        collector = CollectingEventEmitter()
        next_instance = self.state_machine.apply_event(
            definition=definition,
            instance=instance,
            event_type=command.event_type,
            tenant=tenant,
            payload=payload,
            event_emitter=collector,
        )
        saved = self.workflow_repository.save(next_instance, uow=uow)
        events = [
            AuditEvent(
                event_id=event.event_id,
                org_id=event.org_id,
                event_type=event.event_type,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                actor_id=event.actor_id,
                payload=event.payload,
                version=int(saved.version or 1),
                occurred_at=event.occurred_at,
            )
            for event in collector.drain()
        ]
        return Result(value=(saved, False), events=events)
