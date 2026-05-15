from __future__ import annotations

from ngo_homesuite.audit import AuditEvent
from ngo_homesuite.observability import WorkflowTracer
from ngo_homesuite.rbac import can_transition_workflow
from ngo_homesuite.shared_kernel import new_id
from ngo_homesuite.tenant import TenantContext, assert_tenant_match

from .definitions import WorkflowDefinition
from .event_bus import EventEmitter
from .instance import WorkflowInstance, WorkflowStatus


class DeterministicStateMachine:
    """Deterministic workflow runtime using explicit transition rules."""

    def __init__(self, *, event_emitter: EventEmitter, tracer: WorkflowTracer) -> None:
        self._event_emitter = event_emitter
        self._tracer = tracer

    def apply_event(
        self,
        *,
        definition: WorkflowDefinition,
        instance: WorkflowInstance,
        event_type: str,
        tenant: TenantContext,
        payload: dict | None = None,
    ) -> WorkflowInstance:
        assert_tenant_match(instance.org_id, tenant.org_id)
        if not can_transition_workflow(tenant.role, event_type):
            raise PermissionError(f"Role {tenant.role} is not allowed to emit {event_type}")
        if instance.status != WorkflowStatus.ACTIVE:
            raise RuntimeError("Workflow instance is not active")

        rule = definition.transition_for(instance.current_step, event_type)
        if rule is None:
            raise ValueError(
                f"No transition from step {instance.current_step} on event {event_type} in workflow {definition.workflow_type}"
            )

        from_step = instance.current_step
        instance.current_step = rule.to_step
        instance.record_transition(event_type=event_type, from_step=from_step, to_step=rule.to_step)
        target_node = definition.steps.get(rule.to_step)
        if target_node and target_node.terminal:
            instance.status = WorkflowStatus.COMPLETED

        self._tracer.record(
            workflow_instance_id=instance.instance_id,
            org_id=instance.org_id,
            step=instance.current_step,
            event_type=event_type,
        )
        self._event_emitter.emit(
            AuditEvent(
                event_id=new_id("evt"),
                org_id=instance.org_id,
                event_type=event_type,
                aggregate_type="workflow_instance",
                aggregate_id=instance.instance_id,
                actor_id=tenant.user_id,
                payload=payload or {},
            )
        )
        return instance
