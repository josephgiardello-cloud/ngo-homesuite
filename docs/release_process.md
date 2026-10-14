# Release and Versioning Process

Last updated: 2026-10-14

This project uses a lightweight release process to keep rapid changes safe and traceable.

## Versioning

- Use SemVer style tags: `vMAJOR.MINOR.PATCH`.
- `PATCH`: bug fixes and hardening with no intentional behavior break.
- `MINOR`: new features or significant workflow additions.
- `MAJOR`: intentional breaking changes.

## Release Checklist

1. Ensure documentation is current:
- `README.md`
- `docs/full_feature_list.md`
- `docs/feature_status.md`
- `docs/production_checklist.md`
- Relevant ADR updates in `docs/adr/`

2. Validate quality gates:
- Run full tests: `python -m pytest --maxfail=10 -q`
- Default discovery includes both `ngo_homesuite/` and the root `tests/` grant suites.
- Install `pytest`, `pytest-cov`, and `pytest-timeout` in every test lane; coverage and timeout flags are required by `pytest.ini`.
- Run dependency drift guards: `python tools/check_pyproject_requirements_drift.py` and `python tools/check_dependency_drift.py`.
- Run dependency vulnerability checks: `pip-audit -r requirements.txt`.
- Run tenant isolation release lane: `python -m pytest ngo_homesuite/web/test_cross_tenant_boundaries.py ngo_homesuite/web/test_unassigned_tenant_boundaries.py ngo_homesuite/web/test_rbac_tenant_route_audit.py ngo_homesuite/api/test_v1_runtime.py ngo_homesuite/ai/test_minion_routes.py ngo_homesuite/ai/test_minion_tools.py ngo_homesuite/ai/test_semantic_memory.py -v --maxfail=10`
- Run observability release lane: `python -m pytest ngo_homesuite/api/test_observability_api.py ngo_homesuite/api/test_observability_request_logs.py -v --maxfail=10`
- Run mobile/public validation lane: `python -m pytest ngo_homesuite/web/test_mobile_intake_routes.py ngo_homesuite/web/test_public_smoke_paths.py ngo_homesuite/web/test_public_stripe_checkout_routes.py -v --maxfail=10`
- Run workflow reliability failure-injection lane: `python -m pytest ngo_homesuite/tests/test_workflow_reliability_failure_injection.py ngo_homesuite/persistence/test_workflow_repository_concurrency.py ngo_homesuite/tests/test_donation_fund_concurrency.py -v --maxfail=10`
- Run financial concurrency/crash-recovery lane: `python -m pytest ngo_homesuite/tests/test_financial_failure_correctness.py ngo_homesuite/tests/test_bank_reconciliation_service.py ngo_homesuite/services/test_payment_service.py -v --maxfail=10`
- Verify migrations preflight: `python -m ngo_homesuite.db.migrate --dry-run --verify-backup`
- Run non-SQLite deploy migration upgrade when applicable: `python -m ngo_homesuite.db.deploy_migrate upgrade`
- Verify non-SQLite rollback path when applicable: `python -m ngo_homesuite.db.deploy_migrate downgrade --revision -1`
- Run backup/restore drill in non-prod: `python -m ngo_homesuite.db.backup_restore_drill --db data/homesuite.db --output-dir backups/drills`
- Run key-rotation drill in non-prod per `docs/key_rotation_drill.md`
- Run DAST smoke probe and archive report: `python tools/dast_smoke.py --base-url http://127.0.0.1:8765 --report-json artifacts/dast-smoke-report.json`
- Run scalability benchmark and archive report: `python tools/scalability_benchmark.py --base-url http://127.0.0.1:8765 --endpoint /health --endpoint / --endpoint /give --endpoint /p2p/leaderboard --total-requests 300 --concurrency 20 --max-overall-p95-ms 2200 --report-json artifacts/scalability-benchmark.json`
- Run HTTP load smoke against release candidate: `python tools/load_test_smoke.py --base-url http://127.0.0.1:8765 --endpoint /health --endpoint / --endpoint /give`
- Validate release evidence bundle schema and artifact references: `python tools/verify_release_evidence_bundle.py`
- Validate required OpenAPI/runtime route alignment: `python tools/check_openapi_route_drift.py`
- Verify container image passes Docker vulnerability scan in CI

Security testing procedure reference: `docs/security_pentest_playbook.md`
P2P operational procedure reference: `docs/p2p_operations_runbook.md`

3. Prepare changelog summary in release notes:
- Security hardening
- Feature additions
- Data model/migration updates
- Known risks and deferred items

4. Tag and publish:
- Create annotated git tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
- Push tag: `git push origin vX.Y.Z`
- For a pre-release candidate, push an `rc-*` tag (for example `rc-0.2.2-1`) to run the same release gates before publishing a final version.
- Confirm `.github/workflows/release.yml` completed: tests, OpenAPI validation/client generation, Docker scan, release artifact upload
- Release verification also requires the pinned Gitleaks secret scan, dependency drift/vulnerability gates, and the workflow/donation concurrency failure-injection lane.
- Confirm strict release evidence validation passed in release lane: `python tools/verify_release_evidence_bundle.py --strict`
- If strict validation fails only on external pentest evidence, keep release in non-production-candidate status until external sign-off is attached.

## Hotfix Policy

- Hotfixes must include at least one regression test when feasible.
- Do not skip full tests unless CI is degraded; if skipped, document risk and run immediately after restore.

## Documentation Integrity Rule

If feature claims change, update `docs/feature_status.md` in the same change set.

## Gate Regression Coverage

`ngo_homesuite/test_release_gate_policy.py` checks test discovery, pytest plugin
installation, candidate tag triggers, release dependency/secret/reliability gates,
read-only workflow permissions, and Bash selection for the cross-platform job.
These are configuration regression tests, not evidence that the full application
suite or hosted workflows have passed.
The manifest drift guard parses TOML and PEP 508 requirements, follows relative
requirements includes, compares versions and extras, and rejects conflicting pins
or include cycles. Its regression tests are in
`ngo_homesuite/test_pyproject_requirements_drift_guard.py`.

This gate-hardening change preserves the existing Flask/SQLAlchemy runtime and
does not introduce a parallel FastAPI application, change tenant storage, or
replace migration history. Domain/outbox and browser-journey improvements should
be ported through the existing services in subsequent focused changes.

## Financial Failure and Concurrency Contract

Owner: the donation/payment domain services. The authenticated donation JSON
adapter, Stripe completion adapter, and recurring-plan processor use the same
transactional audit helper. No database/schema replacement is required.

- A nonempty `reference_number` identifies a one-off donation request. Identical
  retries return the same donation ID; changed request inputs fail explicitly.
  The authenticated JSON route returns HTTP 409 for reference conflicts. References
  are immutable after creation and remain globally unique under the existing schema.
- New one-off records persist an original request snapshot in their creation audit.
  Legacy records without snapshots compare only the stored donor name, amount,
  currency, donor/campaign/project/fund IDs; full original payloads cannot be
  recovered retroactively. Replays never reset a donation's lifecycle.
- Donation creation, edits, lifecycle changes, payment completion, receipt creation,
  and deletion stage canonical audit records in the same SQLAlchemy transaction.
  Audit failure rolls back the financial mutation and leaves the session usable.
  Recurring charges use a unique plan/scheduled-date reference and commit the
  donation, receipt, audit, and next scheduled date together.
- Monetary inputs must be positive, finite, at most two decimal places, and no
  greater than 999999999.99. Donation storage remains the existing Float schema;
  this is not a migration to a decimal ledger.
- Fund income includes only received/processed/receipted donations, not pending,
  failed, or refunded donations. Existing mixed-currency raw-sum behavior is not
  certified as an FX-aware balance.
- Reconciliation preserves multiplicity and matches identity plus amount first.
  Amount-only fallback requires missing identity on at least one side; conflicting
  explicit IDs or a shared ID with conflicting amounts remain unmatched.
  Non-finite and sub-cent reconciliation amounts are rejected instead of rounded.

Evidence: `ngo_homesuite/tests/test_financial_failure_correctness.py` uses temporary
file-backed SQLite databases, independent sessions synchronized by barriers,
injected ORM/commit failures, and actual subprocess exits without cleanup. It
checks persisted IDs, exact expected amounts, one creation audit per donation,
audit-chain continuity, and duplicate/missing reconciliation entries. A crash
before commit leaves neither donation nor audit; a crash after commit leaves both,
and retry returns the original record. It also exercises the real authenticated
donation JSON route and real Stripe and recurring services (not mocked financial
persistence). The existing reconciliation fallback test now uses absent identities,
not contradictory ones. Both CI and release candidates explicitly run this lane.

Limits: this suite proves the exercised SQLite paths, not all application paths or
production database backends. Unkeyed one-off/public submissions still cannot be
deduplicated reliably; callers must retain and resend a reference. Receipt email
remains synchronous best-effort, not a durable outbox delivery guarantee. The
recurring processor's existing behavior records scheduled charges locally; the
tests do not prove settlement by an external payment provider. No production or
original workspace database is used by the proof suite.

Local verification (2026-10-14, Windows, Python 3.13.7): 72 targeted tests passed
with the repository's coverage/timeout configuration, including the financial
suite, existing stale-update/metrics/reconciliation/payment tests, release-policy
checks, public Stripe routes, donor/donation workflow integration, and legacy
audit tests. The new proof test module passes Ruff; focused mypy passes for the
donation/reconciliation services and proof module. A broader payment-service
type check still reports the pre-existing positional `send_email` signature
error; this is not a full-project type-check certification. The existing optional
CKEditor bleach warning remains. Hosted CI, full-app coverage, and production
database testing were not run locally.

## Confirmed Red-Team Authorization Findings and Fixes

Synthetic local probes confirmed two high-severity authorization failures:
public signup created an authenticated viewer without an organization, which
could inherit the first active tenant on staff pages and bypass API v1's null-org
checks; assigned viewers could also create tasks/donations through donor-profile
POST forms. Valid CSRF tokens were used, so CSRF protection was not the fix.

The authenticated staff-page tenant resolver now requires explicit membership,
API v1 rejects missing organization assignments (including audit and trace reads),
and transactional financial audits reject authenticated actors without a matching
organization. Request middleware distinguishes authenticated missing membership
from anonymous or trusted background execution: application-level ORM isolation
returns no scoped records and rejects tenant writes for unassigned users.
This is defense in depth, not database-native RLS or certification of raw SQL.

Donor-profile POST requires staff/admin authorization before either mutation branch.
Assigned viewers retain read access but do not see quick-entry mutation forms.
Public registration remains available, with an explicit tenant-assignment notice;
signup alone never grants staff-data access. Anonymous public-giving behavior and
trusted background financial processing are preserved.

Regression evidence lives in `ngo_homesuite/web/test_unassigned_tenant_boundaries.py`:
real signup/login, signed CSRF tokens, unaffiliated viewer/staff/admin audit/trace
denials, assigned viewer read-only behavior, staff positive controls, cross-tenant
denials, financial-service rollback, and unscoped ORM read/write boundaries.
Both CI and release tenant gates explicitly include this suite.

Local verification: 144 targeted security, ORM isolation, API workflow, donor
profile, public-giving, financial failure/concurrency, and release-policy tests
passed on Windows/Python 3.13.7. The new boundary test module passes Ruff, and
focused mypy passes for it and the ORM isolation helper. This is not a claim that
every mutating route, hosted CI, or the entire project type-check has passed.

The wider review remains bounded: Minion successful worker execution and actual
SQLCipher rekey/reopen were not established. Rate-limit discrepancies, encryption
attestation/rotation status, malformed inputs, and legacy compliance-audit scoping
were reported as separate investigation leads, not fixed or certified by this
authorization patch. No production probing or production database access occurred.
