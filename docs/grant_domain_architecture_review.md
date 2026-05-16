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
    - `lifecycle.py`: grant lifecycle and financial operations
    - `preaward_impl.py`, `outcomes_impl.py`, `approval_impl.py`, `accounting_policy_impl.py`: grants-owned service implementations
    - `preaward.py`, `outcomes.py`, `approval.py`, `accounting.py`: adapter surfaces used by facade

Facade-only policy for new call-sites:

- Preferred entrypoint: `ngo_homesuite.grants.services.GrantsFacade`
- Avoid calling grant sub-services directly from routes.

Enforcement rule (implemented for web routes):

- Route modules must import grants via `from ngo_homesuite.grants.facade import GrantsFacade`.
- A guard test (`ngo_homesuite/web/test_grant_route_facade_enforcement.py`) fails if web routes directly import legacy `ngo_homesuite.services.grant_*` modules.

Suggested CI grep gate (follow-up):

- Fail build if `ngo_homesuite/web/**/*.py` contains `from ngo_homesuite.services.grant_`.

## Post-B/C Status

Model ownership note:

- Grant-domain SQLAlchemy classes now live in `ngo_homesuite/grants/models.py`.
- `ngo_homesuite/models/core.py` re-exports those classes for backward-compatible imports.

Service ownership note:

- Legacy top-level modules under `ngo_homesuite/services/grant*_service.py` have been removed.
- Grant behavior is now owned by `ngo_homesuite/grants/services/*`.
- Tests and internal callers were migrated to grants-owned imports.

## Static Dependency Snapshot (Grants Package)

- `facade.py` depends on `lifecycle.py` and adapter surfaces (`preaward.py`, `approval.py`, `outcomes.py`, `accounting.py`).
- `lifecycle.py` depends on grants exceptions + grants implementation modules (`preaward_impl.py`, `approval_impl.py`, `outcomes_impl.py`, `accounting_policy_impl.py`).
- Grants services depend on shared infrastructure from `ngo_homesuite.models.core` and `ngo_homesuite.db.utils.audit`.
- No runtime imports remain from `ngo_homesuite.services.grant*_service` modules.

## Major Real-World Compliance Gap Matrix

| Area | Current Model | Real-World Expectation | Gap Severity |
| --- | --- | --- | --- |
| Restricted Funds | Budget lines + allocations + baseline allowability keywords | Full policy engine: allowable/unallowable matrix, indirect exclusions, time-phased drawdowns, carry-forward policy by funder | High |
| Financial Closeout | Blocks closeout on outstanding restricted balance | Final financial + narrative reports, residual handling, equipment disposition, record retention clocks | Medium-High |
| Funder-Specific Requirements | Generic grant/opportunity/proposal fields | Terms storage, award identifiers (e.g., federal assistance listing), custom report schema per funder | Medium |
| Subawards / Pass-through | Not modeled | Subrecipient monitoring, risk tiers, pass-through compliance controls | High (federal) |
| Indirect Costs | Baseline computation in policy service | Negotiated/de minimis rates, allocation basis traceability, exclusions and overrides | Medium-High |
| Procurement Controls | Not integrated | Threshold-driven procurement workflow, vendor conflict screening, bid evidence | Medium |
| Audit Trail Depth | Strong append-only event audit | Evidence linkage + rationale + approval trace for every financial mutation and supporting docs | Medium |
| Multi-year / Amendments | Date fields only | Formal amendment ledger, no-cost extensions, period-specific budget revisions | Medium |
| Match / Cost Share | Not modeled | Cash/in-kind match tracking, ratio validation, reporting rollups | High |

## 2 CFR 200-Oriented Checklist Mapping

Current snapshot against key areas:

- Subpart D (Post Federal Award Requirements): Partial
  - Present: tenant-scoped financial tracking, approval controls, audit trail, closeout guardrails.
  - Missing: subrecipient monitoring lifecycle, formal closeout artifact management, record retention policy metadata.
- Subpart E (Cost Principles): Partial
  - Present: basic unallowable keyword checks + indirect pool baseline.
  - Missing: category-level allowability matrix, negotiated/de minimis rate governance, auditable allocation bases.
- Financial management and internal controls: Partial-Strong
  - Present: SoD/approval chain configuration, escalation queue, immutable decision records.
  - Missing: procurement integration, per-funder compliance profile enforcement.

## Real Grant Mapping Exercise (Initial Results)

Archetype mapping performed at model/field level (code-centric; no production client data ingested):

- Federal archetype (HUD/HHS/SAM-style):
  - Covered: award lifecycle, restricted spending controls, approvals, outcomes.
  - Missing: federal assistance listing/award identifiers, subaward entities, formal closeout package model.
- Foundation archetype (Maine Community Foundation style):
  - Covered: opportunity/proposal pipeline, outcome templates, narrative-ready notes/fields.
  - Missing: per-funder report template schema, amendment/no-cost extension log.
- Corporate archetype:
  - Covered: basic grant lifecycle + spending allocation + outcomes recording.
  - Missing: custom KPI/evidence requirements and match/cost-share controls.

## Highest-Value Data Model Additions

Priority order for next schema wave:

1. `GrantAgreement` / `GrantTerms`
   - Store award IDs, terms, special conditions, reporting cadence, retention period.
2. `GrantAmendment`
   - Formal versioned record of budget/scope/date changes and no-cost extensions.
3. `GrantPeriod`
   - Multi-year periodization for budgets, drawdowns, and reporting.
4. `GrantMatchContribution`
   - Cash/in-kind cost-share tracking with source + valuation + verification.
5. Evidence linkage fields
   - Add supporting-document references and verification status on key financial/outcome records.

## Validation Methods (After B+C)

1. Mock grant exercise
   - Run 3 representative grants (federal/foundation/corporate) end-to-end via facade and record missing fields/events.
2. Mock audit drill
   - Produce all required traces for one grant year: approvals, allocations, closeout evidence, outcomes, policy decisions.
3. User validation
   - Have 1-2 nonprofit operators enter a real historical grant in a dev environment and capture blockers.
4. Static coverage check
   - Track each 2 CFR checklist item against concrete model fields, service methods, and reports.

## Decision Gate (Stop and Evaluate)

After executing the validation methods above:

- Decide whether current 5-service split under `grants/services/` remains maintainable.
- If not, consolidate based on measured coupling and change-frequency (not by adding more adapters).
- Do not add additional abstraction layers before this evidence-based review.

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
