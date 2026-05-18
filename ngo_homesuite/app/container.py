from __future__ import annotations

from dataclasses import dataclass

from ngo_homesuite.audit import DbEventStore
from ngo_homesuite.integration_fabric import ConnectorRegistry
from ngo_homesuite.observability import InMemoryMetrics, WorkflowTracer
from ngo_homesuite.persistence import (
	SqlAlchemyUnitOfWork,
	WorkflowDefinitionRepositoryPort,
	WorkflowRepositoryPort,
	enter_bootstrap_mode,
	exit_bootstrap_mode,
)
from ngo_homesuite.persistence.repositories import WorkflowDefinitionRepository, WorkflowRepository
from ngo_homesuite.shared_kernel import redact_payload
from ngo_homesuite.tenant import TenantContext
from ngo_homesuite.app.write_gate import CollectingEventEmitter, WorkflowWriteCommand, WorkflowWriteHandler, WriteGate
from ngo_homesuite.workflow_engine import (
	DeterministicStateMachine,
	StepNode,
	TransitionRule,
	WorkflowDefinition,
	WorkflowInstance,
)


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
    write_gate: WriteGate

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_repository, WorkflowRepositoryPort):
            raise TypeError("workflow_repository must satisfy WorkflowRepositoryPort")
        if not isinstance(self.workflow_definition_repository, WorkflowDefinitionRepositoryPort):
            raise TypeError("workflow_definition_repository must satisfy WorkflowDefinitionRepositoryPort")

    @classmethod
    def build_default(cls) -> "AppContainer":
        """Build default container. Bootstrap mode disables WriteGate enforcement during app initialization."""
        bootstrap_token = enter_bootstrap_mode()
        try:
            event_store = DbEventStore()
            tracer = WorkflowTracer()
            metrics = InMemoryMetrics()
            workflow_repository = WorkflowRepository()
            workflow_definition_repository = WorkflowDefinitionRepository()
            connector_registry = ConnectorRegistry()
            emitter = CollectingEventEmitter()
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

            write_gate = WriteGate(
                domain_handler=WorkflowWriteHandler(
                    workflow_repository=workflow_repository,
                    state_machine=state_machine,
                    workflow_definitions=workflow_definition_repository.list_active_definitions(allow_global_scope=True),
                ),
                uow=SqlAlchemyUnitOfWork(),
                event_store=event_store,
            )

            return cls(
                event_store=event_store,
                tracer=tracer,
                metrics=metrics,
                connector_registry=connector_registry,
                workflow_repository=workflow_repository,
                workflow_definition_repository=workflow_definition_repository,
                state_machine=state_machine,
                workflow_definitions=workflow_definition_repository.list_active_definitions(allow_global_scope=True),
                write_gate=write_gate,
            )
        finally:
            exit_bootstrap_mode(bootstrap_token)

    def create_workflow_instance(self, *, org_id: str, workflow_type: str) -> WorkflowInstance:
        result = self.write_gate.execute(
            WorkflowWriteCommand(
                action="create_workflow_instance",
                org_id=org_id,
                actor_id="system",
                role="org_admin",
                workflow_type=workflow_type,
            )
        )
        return result.value

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
        result = self.write_gate.execute(
            WorkflowWriteCommand(
                action="dispatch_workflow_event",
                org_id=tenant.org_id,
                actor_id=tenant.user_id,
                role=tenant.role,
                instance_id=instance_id,
                event_type=event_type,
                payload={**payload, **({"_idempotency_key": idempotency_key} if idempotency_key else {})},
                idempotency_key=idempotency_key,
            )
        )
        return result.value
