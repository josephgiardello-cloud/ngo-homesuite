# Grant Domain Architecture Review (Consolidation-First)

Date: 2026-05-16

## Domain Module Structure (Implemented)

The grant bounded context now has a dedicated package:

- `ngo_homesuite/grants/`
  - `models.py`: canonical grant model definitions and ownership point
  - `exceptions.py`: grant-specific exception access
  - `types.py`: grant value types/enums
  - `audit.py`: grant audit payload helpers
  - `invariants.py`: cross-service invariants
  - `services/`
    - `facade.py`: `GrantsFacade` thin orchestrator entrypoint
    - `preaward.py`, `outcomes.py`, `approval.py`, `accounting.py`: domain service adapters

Facade-only policy for new call-sites:

- Preferred entrypoint: `ngo_homesuite.grants.services.GrantsFacade`
- Avoid calling grant sub-services directly from routes.

Enforcement rule (implemented for web routes):

- Route modules must import grants via `from ngo_homesuite.grants.facade import GrantsFacade`.
- A guard test (`ngo_homesuite/web/test_grant_route_facade_enforcement.py`) fails if web routes directly import legacy `ngo_homesuite.services.grant_*` modules.

Suggested CI grep gate (follow-up):

- Fail build if `ngo_homesuite/web/**/*.py` contains `from ngo_homesuite.services.grant_`.

## Current Service Ownership Boundaries

Model ownership note:

- Grant-domain SQLAlchemy classes now live in `ngo_homesuite/grants/models.py`.
- `ngo_homesuite/models/core.py` re-exports those classes for backward-compatible imports.

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

1. Migrate route handlers to `GrantsFacade` as sole grant-domain entrypoint.
2. Introduce transaction helper conventions for approval-gated financial operations.
3. Add concurrency tests for conflicting approve/execute operations.
4. Add mutation-level docstrings to facade wrappers that identify owning sub-service.

## Commit C Progress (Legacy Service Retirement)

- `ExpenseService` grant allocation path now calls `GrantsFacade` instead of direct `grant_service` import.
- Canonical grant exceptions now live in `ngo_homesuite/grants/exceptions.py`.
- `ngo_homesuite/services/grant_service.py` now imports canonical exception classes from grants domain.

Remaining retirement work:

- Reduce `GrantsFacade` lifecycle dependency on `ngo_homesuite/services/grant_service.py` by moving lifecycle implementation into grants domain modules.
- Migrate grant-focused tests from direct `grant_service` imports to facade-focused imports where monkeypatch behavior permits.

## Compliance Gap Snapshot

- Stronger now: configurable approval chains, escalation SLA queue processing, richer SoD checks, baseline accounting policies.
- Remaining gaps: conditional branches beyond amount thresholds, parallel approval semantics, full federal allowability matrix, real-world tenant validation.
