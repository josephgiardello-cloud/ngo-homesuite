from __future__ import annotations

from dataclasses import dataclass

from ngo_homesuite.audit import DbEventStore
from ngo_homesuite.integration_fabric import ConnectorRegistry
from ngo_homesuite.observability import InMemoryMetrics, WorkflowTracer
from ngo_homesuite.persistence import SqlAlchemyUnitOfWork, WorkflowDefinitionRepositoryPort, WorkflowRepositoryPort
from ngo_homesuite.persistence.repositories import WorkflowDefinitionRepository, WorkflowRepository
from ngo_homesuite.shared_kernel import new_id, redact_payload
from ngo_homesuite.tenant import TenantContext
from ngo_homesuite.workflow_engine import (
    DeterministicStateMachine,
    StepNode,
    TransitionRule,
    WorkflowDefinition,
    WorkflowInstance,
)
from ngo_homesuite.workflow_engine.event_bus import EventEmitter


@dataclass
class AppContainer:
    event_store: DbEventStore
    tracer: WorkflowTracer
    metrics: InMemoryMetrics
    connector_registry: ConnectorRegistry
    workflow_repository: WorkflowRepositoryPort
    workflow_definition_repository: WorkflowDefinitionRepositoryPort
    state_machine: DeterministicStateMachine
    workflow_definitions: dict[str, WorkflowDefinition]

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_repository, WorkflowRepositoryPort):
            raise TypeError("workflow_repository must satisfy WorkflowRepositoryPort")
        if not isinstance(self.workflow_definition_repository, WorkflowDefinitionRepositoryPort):
            raise TypeError("workflow_definition_repository must satisfy WorkflowDefinitionRepositoryPort")

    @classmethod
    def build_default(cls) -> "AppContainer":
        event_store = DbEventStore()
        tracer = WorkflowTracer()
        metrics = InMemoryMetrics()
        workflow_repository = WorkflowRepository()
        workflow_definition_repository = WorkflowDefinitionRepository()
        connector_registry = ConnectorRegistry()
        emitter = EventEmitter(event_store)
        state_machine = DeterministicStateMachine(event_emitter=emitter, tracer=tracer, metrics=metrics)

        intake_flow = WorkflowDefinition(
            workflow_type="case_intake",
            initial_step="intake",
            steps={
                "intake": StepNode("intake"),
                "verification": StepNode("verification"),
                "assignment": StepNode("assignment"),
                "resolution": StepNode("resolution"),
                "closure": StepNode("closure", terminal=True),
            },
            transitions=[
                TransitionRule("intake", "intake_submit", "verification"),
                TransitionRule("verification", "intake_verify", "assignment"),
                TransitionRule("assignment", "intake_assign", "resolution"),
                TransitionRule("resolution", "case_resolve", "closure"),
            ],
        )
        donation_flow = WorkflowDefinition(
            workflow_type="donation_fulfillment",
            initial_step="donation",
            steps={
                "donation": StepNode("donation"),
                "receipt": StepNode("receipt"),
                "allocation": StepNode("allocation"),
                "reporting": StepNode("reporting", terminal=True),
            },
            transitions=[
                TransitionRule("donation", "donation_receipt", "receipt"),
                TransitionRule("receipt", "donation_allocate", "allocation"),
                TransitionRule("allocation", "donation_report", "reporting"),
            ],
        )

        with SqlAlchemyUnitOfWork() as uow:
            workflow_definition_repository.ensure_definition(intake_flow, allow_global_scope=True, uow=uow)
            workflow_definition_repository.ensure_definition(donation_flow, allow_global_scope=True, uow=uow)

        return cls(
            event_store=event_store,
            tracer=tracer,
            metrics=metrics,
            connector_registry=connector_registry,
            workflow_repository=workflow_repository,
            workflow_definition_repository=workflow_definition_repository,
            state_machine=state_machine,
            workflow_definitions=workflow_definition_repository.list_active_definitions(allow_global_scope=True),
        )

    def create_workflow_instance(self, *, org_id: str, workflow_type: str) -> WorkflowInstance:
        if not str(org_id).strip():
            raise PermissionError("Tenant isolation requires non-empty org_id")
        definition = self.workflow_definitions.get(workflow_type)
        if definition is None:
            raise KeyError(f"Unknown workflow type: {workflow_type}")

        instance = WorkflowInstance(
            instance_id=new_id("wfi"),
            org_id=org_id,
            workflow_type=workflow_type,
            current_step=definition.initial_step,
        )
        with SqlAlchemyUnitOfWork() as uow:
            return self.workflow_repository.save(instance, uow=uow)

    @staticmethod
    def _is_idempotent_replay(instance: WorkflowInstance, *, event_type: str, idempotency_key: str | None) -> bool:
        if not idempotency_key:
            return False
        for item in instance.history:
            payload = item.get("payload") if isinstance(item, dict) else None
            if not isinstance(payload, dict):
                continue
            if item.get("event_type") == event_type and payload.get("_idempotency_key") == idempotency_key:
                return True
        return False

    def dispatch_workflow_event(
        self,
        *,
        instance_id: str,
        event_type: str,
        tenant: TenantContext,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[WorkflowInstance, bool]:
        payload = redact_payload(payload or {})
        instance = self.workflow_repository.get(instance_id, org_id=tenant.org_id)
        if instance is None:
            raise KeyError(f"Unknown workflow instance: {instance_id}")
        if self._is_idempotent_replay(instance, event_type=event_type, idempotency_key=idempotency_key):
            return instance, True
        if idempotency_key:
            payload["_idempotency_key"] = idempotency_key
        definition = self.workflow_definitions[instance.workflow_type]
        next_instance = self.state_machine.apply_event(
            definition=definition,
            instance=instance,
            event_type=event_type,
            tenant=tenant,
            payload=payload,
        )
        with SqlAlchemyUnitOfWork() as uow:
            saved = self.workflow_repository.save(next_instance, uow=uow)
        return saved, False
