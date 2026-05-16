# Grant Domain Architecture Review (Consolidation-First)

Date: 2026-05-16

## Current Service Ownership Boundaries

- `ngo_homesuite/services/grant_service.py`
  - Aggregate facade for grant lifecycle transitions, budget lines, allocations, disbursements, and reporting utilities.
  - Integrates approval gates and accounting policy checks.
- `ngo_homesuite/services/grant_preaward_service.py`
  - Opportunity/proposal lifecycle, conversion to awarded grants.
- `ngo_homesuite/services/grant_outcomes_service.py`
  - Outcomes templates, outcome records, variance reporting.
- `ngo_homesuite/services/grant_approval_service.py`
  - Approval requests/decisions, per-org chain config, SoD enforcement, escalation/expiry SLA processing.
- `ngo_homesuite/services/grant_accounting_policy_service.py`
  - Allowable cost policy checks and accounting foundation calculations (carry-forward, indirect pool).

## Observed Architectural Risks

- High call-surface fan-out from `grant_service` into four grant sub-services.
- Approval state transitions and financial side effects are still cross-service and can drift if wrappers are bypassed.
- Approval policies are now persistent but still code-first (no admin route/UI layer yet).

## Duplicated or Chatty Patterns

- Facade wrappers in `grant_service` now mirror methods from `grant_preaward_service` and `grant_approval_service`.
- Create/decide/execute flows involve multiple commits by design (audit visibility), increasing consistency pressure under concurrent workers.

## Consolidation Direction (No New Framework Layer)

- Keep one explicit aggregate entrypoint: `grant_service` for all externally-called grant mutations.
- Keep specialized policy engines (`grant_approval_service`, `grant_accounting_policy_service`) pure-domain and side-effect bounded.
- Restrict direct route/web usage to `grant_service`; prevent routes calling sub-services directly.

## Aggregate Boundaries

- `GrantAggregate`
  - Grant, disbursements, budget lines, allocations.
- `PreAwardAggregate`
  - Opportunities and proposals.
- `ApprovalAggregate`
  - Approval requests, decisions, chain configs.
- `OutcomesAggregate`
  - Outcome templates and records.

## Immediate Cleanup Tasks

1. Add route-level guardrails so grant mutations use `grant_service` facade only.
2. Introduce transaction helper conventions for approval-gated financial operations.
3. Add concurrency tests for conflicting approve/execute operations.
4. Add mutation-level docstrings to `grant_service` wrappers that identify owning sub-service.

## Compliance Gap Snapshot

- Stronger now: configurable approval chains, escalation SLA queue processing, richer SoD checks, baseline accounting policies.
- Remaining gaps: conditional branches beyond amount thresholds, parallel approval semantics, full federal allowability matrix, real-world tenant validation.
