from __future__ import annotations

import pytest

from ngo_homesuite.audit import InMemoryEventStore
from ngo_homesuite.observability import InMemoryMetrics, WorkflowTracer
from ngo_homesuite.rbac import Role
from ngo_homesuite.tenant import TenantContext
from ngo_homesuite.workflow_engine import (
    DeterministicStateMachine,
    StepNode,
    TransitionRule,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowStatus,
)
from ngo_homesuite.workflow_engine.event_bus import EventEmitter


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_type="case_intake",
        initial_step="intake_started",
        steps={
            "intake_started": StepNode(name="intake_started"),
            "intake_verified": StepNode(name="intake_verified"),
            "closed": StepNode(name="closed", terminal=True),
        },
        transitions=[
            TransitionRule(from_step="intake_started", event_type="intake_verify", to_step="intake_verified"),
            TransitionRule(from_step="intake_verified", event_type="case_close", to_step="closed"),
        ],
    )


def test_state_machine_applies_transition_records_trace_and_event() -> None:
    store = InMemoryEventStore()
    tracer = WorkflowTracer()
    metrics = InMemoryMetrics()
    machine = DeterministicStateMachine(event_emitter=EventEmitter(store), tracer=tracer, metrics=metrics)

    instance = WorkflowInstance(
        instance_id="wf_1",
        org_id="org-1",
        workflow_type="case_intake",
        current_step="intake_started",
    )
    tenant = TenantContext(org_id="org-1", user_id="user-1", role=Role.CASE_WORKER.value)

    updated = machine.apply_event(
        definition=_definition(),
        instance=instance,
        event_type="intake_verify",
        tenant=tenant,
        payload={"source": "mobile_intake"},
    )

    assert updated.current_step == "intake_verified"
    assert updated.status == WorkflowStatus.ACTIVE
    assert updated.history[-1]["event_type"] == "intake_verify"

    events = store.list_events(org_id="org-1", aggregate_id="wf_1")
    assert len(events) == 1
    assert events[0].event_type == "intake_verify"
    assert events[0].actor_id == "user-1"

    trace = tracer.get("wf_1")
    assert trace is not None
    assert trace.org_id == "org-1"
    assert trace.steps[-1]["step"] == "intake_verified"

    rendered = metrics.render_prometheus()
    assert "workflow_events_total" in rendered
    assert "workflow_event_latency_ms" in rendered


def test_state_machine_blocks_cross_tenant_event() -> None:
    machine = DeterministicStateMachine(
        event_emitter=EventEmitter(InMemoryEventStore()),
        tracer=WorkflowTracer(),
        metrics=InMemoryMetrics(),
    )
    instance = WorkflowInstance(
        instance_id="wf_2",
        org_id="org-1",
        workflow_type="case_intake",
        current_step="intake_started",
    )

    with pytest.raises(PermissionError, match="Tenant isolation violation"):
        machine.apply_event(
            definition=_definition(),
            instance=instance,
            event_type="intake_verify",
            tenant=TenantContext(org_id="org-2", user_id="user-2", role=Role.CASE_WORKER.value),
        )


def test_state_machine_records_error_metric_for_rejected_role() -> None:
    metrics = InMemoryMetrics()
    machine = DeterministicStateMachine(
        event_emitter=EventEmitter(InMemoryEventStore()),
        tracer=WorkflowTracer(),
        metrics=metrics,
    )
    instance = WorkflowInstance(
        instance_id="wf_3",
        org_id="org-1",
        workflow_type="case_intake",
        current_step="intake_started",
    )

    with pytest.raises(PermissionError, match="not allowed"):
        machine.apply_event(
            definition=_definition(),
            instance=instance,
            event_type="intake_verify",
            tenant=TenantContext(org_id="org-1", user_id="viewer-1", role="viewer"),
        )

    rendered = metrics.render_prometheus()
    assert "workflow_event_errors_total" in rendered
