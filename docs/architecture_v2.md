# NGO HomeSuite V2 Architecture Blueprint

This document codifies the V2 target as a buildable system baseline.

## Core Principle

NGO HomeSuite V2 is a multi-tenant NGO operating system with:

- domain kernel
- deterministic workflow runtime
- integration fabric
- append-only event audit

## System Layers

1. Domain kernel: ngo_homesuite/domain/kernel.py
2. Workflow execution engine: ngo_homesuite/workflow_engine/
3. Integration fabric: ngo_homesuite/integration_fabric/
4. Data layer and repositories: ngo_homesuite/persistence/
5. Security and tenant/RBAC: ngo_homesuite/tenant/, ngo_homesuite/rbac/
6. API layer: ngo_homesuite/api/v1.py
7. Observability: ngo_homesuite/observability/

## Domain Invariants

- All state transitions are represented as workflow events.
- Every workflow transition emits an append-only audit event.
- Tenant boundaries are enforced by org_id checks before transitions.
- Role permissions gate workflow transitions.

## Execution Model

Event -> Workflow transition -> State update -> Trace append -> Audit event append -> API response

## Roadmap Fit

This implementation establishes Phase 1 and Phase 2 foundations:

- domain kernel entities and tenant root
- deterministic workflow definitions and runtime
- event emitter + append-only event store
- versioned API endpoints for workflows and audit queries

Next implementation phases should focus on:

- DB-backed event store and projections
- field-level PII policy enforcement
- connector adapters for Stripe/Twilio/SES/Excel
- workflow DSL and visual builder
