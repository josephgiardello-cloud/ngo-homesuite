# Production Checklist

Last updated: 2026-05-31

Use this checklist before production deployment.

## Security and Secrets

- Set explicit `SECRET_KEY` and `DATABASE_URL` in production.
- For Docker Compose, set a strong `POSTGRES_PASSWORD` (do not use placeholders/defaults).
- If using SQLCipher, store `NGO_HOMESUITE_DB_KEY` in a secret manager.
- Document key recovery and rotation steps.
- Disable any emergency fallback flags after incident recovery.
- Run the security release lane: CI secret scan, `pip-audit`, Bandit, AI/auth hardening tests, and archive the release evidence bundle.

## Database and Migrations

- Run migration preflight: `python -m ngo_homesuite.db.migrate --dry-run --verify-backup`.
- Confirm backup policy is active before migration.
- Validate restore path from latest backup in a non-prod environment.
- Use PostgreSQL as the default production backend; use SQLite only for demos/local quickstarts.

## Container Runtime Hardening

- Verify container runs non-root with `read_only` root filesystem where applicable.
- Ensure memory/CPU/PID limits are configured for app and data services.
- Keep healthchecks enabled and alert on repeated unhealthy states.

## Multi-Tenant Safety

- Verify org scoping on all list/search/report endpoints.
- Verify AI/RAG retrieval context is tenant-scoped.
- Run cross-tenant negative tests for mutating endpoints.
- Run the tenant isolation release lane: cross-tenant route tests, API runtime tenant-boundary tests, and AI tenant-scoping checks.

## Reliability and Operations

- Run full test suite in CI on release branch.
- Add smoke tests for public donation and P2P paths.
- Run DAST smoke checks and store artifacts for each release candidate.
- Run scalability benchmark regression checks against baseline thresholds.
- Configure process supervisor with restart policy and liveness checks.
- Confirm backup job schedule and retention policy.

## Observability

- Forward application logs to centralized storage (for example ELK/Azure Monitor).
- Expose and scrape metrics endpoint for alerting.
- Alert on migration failures, repeated DB lock errors, and key integrity checks.
- Run the observability release lane: request-ID propagation tests, metrics endpoint assertions, and monitoring alert-rule validation.

## UX and Workflow Validation

- Test public forms on mobile breakpoints.
- Validate donor-facing copy, receipts, and redirect paths.
- Run an operator walkthrough for core flows: donor intake, donation, P2P campaign, reporting export.

## Release Gate

A release is production-ready only when:
- All required checklist items are complete.
- Open high-severity issues are resolved or explicitly accepted.
- Feature maturity labels are current in `docs/feature_status.md`.

## Release Gate Matrix (Verified 2026-05-18)

Status legend: `PASS`, `PARTIAL`, `FAIL`.

| Gate | Status | Evidence | Remaining blocker(s) |
|---|---|---|---|
| Auth hardening (MFA capability) | PARTIAL | TOTP/backup-code/2FA enforcement and step-up endpoints exist in `ngo_homesuite/web/auth_routes.py` and user fields exist in `ngo_homesuite/models/core.py`. Route-level step-up audit has been applied across high-risk destructive/export endpoints in `main_routes.py`, `reporting_routes.py`, `admin_grants_routes.py`, `program_routes.py`, `volunteer_routes.py`, and `v2_routes.py`. | TOTP secret encryption-at-rest path still needs explicit production documentation/evidence. |
| External security assurance | FAIL | `docs/security_pentest_playbook.md` defines process and artifacts. | No in-repo evidence bundle of completed external formal security review/pentest sign-off. |
| Tenant isolation and RBAC | PARTIAL | Cross-tenant and route-policy test lanes exist (`ngo_homesuite/web/test_cross_tenant_boundaries.py`, RBAC audit tests). | Continue exhaustive mutator-path verification and preserve AI-specific tenant-isolation evidence. |
| Grants accounting readiness | PARTIAL | Budget lines, transactions, and reconciliation fields/models exist under `ngo_homesuite/grants/models.py` and migrations `0022/0023/0025`; lifecycle variance logic is implemented. | Full operational workflows/report UX and state/funder-specific automation remain incomplete. |
| Bulk campaign maturity | PARTIAL | Campaign batch/delivery models, unsubscribe, suppression, and open/click metrics exist (`ngo_homesuite/models/core.py`, `ngo_homesuite/services/campaign_email_service.py`, `ngo_homesuite/web/v2_routes.py`). | Advanced segmentation/query builder and full queue-orchestration model from backlog remain incomplete. |
| Observability and alerting | PARTIAL | Logging/metrics stack artifacts are present; baseline checks documented. | Centralized log/alert tuning and release-grade evidence validation remain open. |
| Backup/restore + key rotation drills | PARTIAL | Runbooks and migration preflight checks exist. | Non-prod drill evidence must be attached per release candidate. |
| Container hardening | PARTIAL | Checklist requirements are documented. | Explicit non-root/read-only/resource-limit verification evidence required per release. |
| Public-path security and smoke | PARTIAL | DAST smoke tooling and process are documented; local artifacts exist under `artifacts/`. | Require repeatable release-candidate artifacts and sign-off for all public paths. |
| Mobile/public form validation | FAIL | Checklist requirement exists. | No attached release-evidence bundle for complete mobile/public-form validation. |

## Additional Incomplete Areas Identified (Repo Scan)

- Root-level debug/probe scripts remain in large volume (for example `_trace_*`, `_tmp_*`, `_dash_*`, `_manual_*`). These should be moved to a clearly non-production tooling area or excluded from release workflows.
- Legacy compatibility and fallback surfaces remain active (runtime compat mode toggle, legacy schema fallback controls). Keep these tightly controlled and audited for production use.
- Integration journey coverage currently provides a baseline donor->donation->receipt path; additional end-to-end scenarios are still required for full release confidence.
- Selected RBAC audit scenarios still skip when legacy modules/endpoints are unavailable (`ngo_homesuite/web/test_rbac_audit_matrix.py`), indicating partial test-lane coverage.

## Latest Validation Evidence (2026-05-18)

Targeted release-gate lane executed:

```bash
.venv\Scripts\python.exe -m pytest ngo_homesuite/web/test_auth_security_routes.py ngo_homesuite/web/test_cross_tenant_boundaries.py ngo_homesuite/web/test_v2_route_contracts.py ngo_homesuite/web/test_rbac_audit_matrix.py ngo_homesuite/web/test_campaign_routes.py ngo_homesuite/tests/test_grant_budget_line_accounting.py ngo_homesuite/web/test_grants_route_contracts.py -v -rs --maxfail=20
```

Outcome:

- `112 passed`
- `2 skipped`
- `0 failed`
- runtime: `26.05s`

Skipped tests and reasons:

1. `ngo_homesuite/web/test_rbac_audit_matrix.py::TestRbacAuditMatrix::test_role_assignment_audit_enforcement`
	- Reason: `Legacy EventLog persistence module not available in this runtime`
2. `ngo_homesuite/web/test_rbac_audit_matrix.py::TestSecureBootstrapFlow::test_first_admin_bootstrap_one_time_token`
	- Reason: `Bootstrap endpoint not yet implemented`

## Latest Validation Evidence (2026-05-31)

Targeted security-hardening validation executed:

```bash
.venv\Scripts\python.exe -m pytest ngo_homesuite/web/test_donor_delete_routes.py -v --maxfail=1
```

Outcome:

- `1 passed`
- `0 skipped`
- `0 failed`

Security-hardening behavior validated in this lane:

- donor delete path executes under step-up constraints and removes same-org donor rows as expected.
- scheduled-report deletion route now requires step-up auth and passes targeted lifecycle coverage (`ngo_homesuite/web/test_roadmap_tranche4.py::TestScheduledReports::test_scheduled_report_full_lifecycle`).
- grant budget-line deletion route now requires step-up auth and passes targeted admin-route coverage (`tests/test_grant_budget_model.py::TestGrantBudgetAdminRoutes::test_delete_budget_line_no_allocations`).
- program case deletion and intake-beneficiary deletion routes now require step-up auth and pass focused lifecycle coverage (`ngo_homesuite/web/test_program_routes.py::test_program_case_management_routes`).
- volunteer training-course deletion route now requires step-up auth and passes focused lifecycle coverage (`ngo_homesuite/web/test_volunteer_accounting.py::TestTrainingCourses::test_course_full_lifecycle`).
- appointment cancellation route now requires step-up auth and passes focused lifecycle coverage (`ngo_homesuite/web/test_roadmap_tranche4.py::TestAppointmentRoutes::test_appointment_full_lifecycle`).
- v2 campaign email segment deletion now requires step-up auth and passes focused route coverage (`ngo_homesuite/web/test_campaign_routes.py::test_campaign_email_segment_delete_endpoint_removes_segment`).

## Explicit Production-Release Blockers

The following items are considered **blocking** for any production release until resolved or formally risk-accepted by owners.

| Weakness | Why it's critical |
|---|---|
| Integration journey depth is still narrow | Baseline flow exists, but broader end-to-end coverage (payment failures, retries, role boundaries, and recovery drills) is needed to reduce release risk. |
| RBAC audit lane conditional skips (`ngo_homesuite/web/test_rbac_audit_matrix.py:314`, `ngo_homesuite/web/test_rbac_audit_matrix.py:377`) | RBAC is not fully validated. If skip conditions trigger in production-like environments, role enforcement regressions can go undetected. |
| Legacy fallback surfaces active | Can reintroduce old or weaker behavior under failure scenarios. Requires explicit governance (feature flag control + structured logged warning + operator runbook). |
| Debug scripts in repository root | Release-hygiene and operational risk; scripts can be accidentally invoked, confuse runbooks, or expose internals during incident response. |
