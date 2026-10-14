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
- Run financial concurrency/crash-recovery lane: `python -m pytest ngo_homesuite/tests/test_financial_failure_correctness.py ngo_homesuite/tests/test_manual_recurring_and_currency.py ngo_homesuite/tests/test_financial_audit_privacy.py ngo_homesuite/tests/test_bank_reconciliation_service.py ngo_homesuite/services/test_payment_service.py -v --maxfail=10`
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
- Run reviewed tenant disposition gate: `python tools/verify_tenant_dispositions.py --strict`.
- Run organization/legal retention approval gate: `python tools/verify_audit_retention_policy.py --strict`.
- Run `Candidate Database Isolation` on the exact committed SHA for both PostgreSQL and SQLite/SQLCipher. Its JUnit manifests reject failures, unexpected skips, SHA mismatches and modified tracked files.
- Require full fetched Git history for Gitleaks, including all fetched heads/tags, not only the candidate/PR range. Historical exceptions must identify reviewed finding fingerprints, never whole source paths.

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
- New one-off records persist a versioned, salted canonical request digest in their creation audit.
  Legacy records without replay evidence compare only the stored donor name, amount,
  currency, donor/campaign/project/fund IDs; full original payloads cannot be
  recovered retroactively. Replays never reset a donation's lifecycle.
- Donation creation, edits, lifecycle changes, payment completion, receipt creation,
  and deletion stage canonical audit records in the same SQLAlchemy transaction.
  Audit failure rolls back the financial mutation and leaves the session usable.
  Manual recurring occurrences use a unique plan/scheduled-date reference.
  Preparation commits only a pending donation and its audit; confirmed collection
  commits received status, its audit and the next calendar date together. Receipt
  generation is a separate policy-controlled operation, never proof of charging.
- Monetary inputs must be positive, finite, at most two decimal places, and no
  greater than 999999999.99. Donation storage remains the existing Float schema;
  this is not a migration to a decimal ledger.
- Fund income includes only received/processed/receipted donations, not pending,
  failed, or refunded donations. Fund balances are separated by original currency;
  other mixed-currency organization-wide report totals are not certified as FX-aware.
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
recurring scheduler now prepares pending manual collections, not charges;
confirmation is staff attestation, not proof of settlement from a provider. No production or
original workspace database is used by the proof suite.

### Manual recurring collections and original-currency fund balances

`DonationService` owns collection lifecycle invariants. The staff web routes
validate CSRF/forms and delegate to it. `schedule_due_manual_collections` creates
at most one pending donation per plan/due date, with a canonical creation audit
in the same transaction. It never invokes a payment provider, issues a receipt,
counts expected money as received, or advances the date. The legacy
`process_due_recurring_plans` adapter has the same pending-only behavior;
its `processed` count means bookkeeping records prepared, not successful charges.

`confirm_manual_collection` requires an active tenant staff/admin actor, an
external collection evidence reference, and the exact expected amount/currency.
It atomically records received income, advances the calendar-month/quarter/year
schedule, and appends a canonical confirmation audit. Identical retries reuse
the confirmation; conflicting evidence is rejected. Confirmation replays cannot
resurrect refunds. Failed collections may be confirmed after real collection;
paused/cancelled plans cannot be confirmed. Generic status changes and provider
webhooks cannot bypass this operation. Scheduled financial identity/deletion is
blocked to preserve occurrence idempotency. The receipt workflow remains
separate; policy-specific tax acknowledgments are not inferred.

Frequency values are validated (`monthly`, `quarterly`, `yearly`). A date-only
UTC calendar schedule retains its original anchor day in the occurrence audit:
January 31 becomes February 28/29 and then March 31; a February 29 yearly
anchor returns to February 29 in leap years. No local-time/DST instant is stored.
Missed runs prepare only the oldest outstanding occurrence, never falsely collect
every missed period or skip ahead. Each real confirmation advances one period.
Legacy plans use their first newly scheduled occurrence as the anchor; fixed-day
drift already present in historical schedules requires operator reconciliation.

The donor-facing recurring option explicitly describes manual collection.
Fund balances and the exercised financial dashboard/overview/purpose/foundation
reports exclude pending, failed, and refunded donations from income. Historical
`receipted` recurring entries created by the old processor are NOT automatically
rewritten: reconcile them against external payment evidence before production.

`FundService.get_fund_balance` returns `balances_by_currency`, keyed by original
three-letter currency with `total_in`, `total_out`, and `net_balance` per currency.
Amounts use decimal accumulation without changing original rows. Legacy scalar
totals remain available for empty/single-currency funds, with an explicit
`currency` for the latter. Mixed-currency scalar totals are `None`. Callers must
consume the grouped output and must not turn `None` into a misleading combined
total. No FX conversion or consolidated multi-currency report certification is
claimed; other organization-wide report totals still need an explicit currency
policy before they are used for multi-currency statutory reporting.

Regression contracts live in `test_manual_recurring_and_currency.py`:
simultaneous confirmation, audit-insert rollback, process exits before commit,
after audit insertion and after commit, retry/refund behavior, tenant/role
denials, amount/currency/evidence validation, and USD/EUR income/expense grouping.
Real CSRF-enabled staff route controls are exercised in
`web/test_unassigned_tenant_boundaries.py`.

### Financial audit privacy and monitoring

New donation creation audits retain only amount/currency and entity IDs plus a
versioned canonical SHA-256 request digest with a per-request random salt. Version
2 binds the request to a fixed purpose, tenant and donation reference. Canonical
JSON is sorted/compact, rejects non-finite numbers and uses a cryptographically
random 256-bit public salt; comparisons use `hmac.compare_digest`. Version 1
remains readable, while corrupt or unsupported replay metadata fails explicitly. The
digest preserves full normalized replay comparison without copying email,
phone, free-text notes, tribute details, or honoree contact into the audit.
Manual collection confirmation retains an evidence-reference digest, not the
reference text; the authoritative external evidence remains outside this audit.
Salting request snapshots reduces cross-record correlation; it is not encryption or a promise
that low-entropy values cannot be guessed by a reader of the digest. Update
audits retain changed field names, not their values. Existing snapshot audits
remain readable for compatibility; they are not silently rewritten or deleted.
The public salt is not a secret key or HMAC and does not prevent guessing low-entropy
requests. Existing chain linkage does not authenticate arbitrary audit payload
contents. Replay digests are not a substitute for database access control, protected
backups or retention approval.

Financial actions have distinct event types for creation, update, collection,
status transition, refund, receipt generation, and deletion. Reconciliation
audits retain an operation ID, tenant ID, result counts and salted input
digests, never full bank/ledger input text. Detailed unmatched entries returned
to the caller remain sensitive and must not be copied into general logs.

Before production the organization's security/compliance owner must approve an
explicit audit retention period, legal-hold rules, export/backup access,
deletion/archive procedure and audit-chain boundary preservation. No default
deletion period is assumed and no historical audit purge is performed. Audit
read/export access is for tenant administrators and explicitly authorized
compliance operators, not ordinary staff/viewers or AI tools. Current raw SQL,
legacy export and database-native enforcement coverage is incomplete; this
policy is a release prerequisite, not a claim all such access paths are fixed.
The canonical audit query service now rejects anonymous, non-admin and
cross-tenant request actors; trusted in-process audit maintenance is still a
separate explicit-authorization responsibility.

### Python 3.13 SQLCipher adapter evidence

The encrypted Linux x86_64 CI image installs `sqlcipher3-binary==0.6.0`, not the
legacy C extension that fails to build on Python 3.13. The application adapter
prefers `sqlcipher3`, supports explicit legacy `pysqlcipher3` installations, and
requires a nonempty `PRAGMA cipher_version` result. A plain SQLite connection
cannot attest encryption. Keys are applied before database reads/WAL setup,
and SQLCipher's own DB-API error classes participate in failure handling.
CI generates a disposable key at container startup; no key is baked into the
image. The encrypted image requires real encryption tests instead of skips.

`db/test_sqlcipher_adapter.py` exercises real hex/passphrase encrypted files,
SQLCipher version, ciphertext header, ordinary SQLite rejection, wrong-key
rejection, successful reopen, and real rekey/new-key reopen/old-key rejection
on Python 3.13. It also tests modern/legacy selection and missing-driver
fail-closed behavior. Tests use synthetic temporary data only. Schema checks
remain enabled when reopening; rotation tests use an already-established
provenance schema. This does not certify every Flask ORM persistence path,
Windows encryption, legacy database upgrades, rotation with in-flight workers,
or production key custody. Those remain separate release evidence requirements.

The repeatable full tenant inventory and explicitly unverified surfaces are in
`docs/tenant_boundary_inventory.md` and its JSON companion. The 67 direct-model
ORM registry, four indirect chains, strict background SQL boundary and 71 forced
PostgreSQL policies have persisted proof. Event/campaign/grant schedulers use an
explicit organization allowlist and durable start/finish audit records. Legacy
unowned event SQL is rejected before dispatch; this is not a functioning tenant
migration of those queues. Other workers/legacy connections and the complete
surface review remain blockers. Request SQL guarding is opt-in and cannot be
enabled blindly for tenantless auth/global/bootstrap/public paths.
The exact candidate's 32 failed CI jobs and secret-scan classification are
documented in `docs/ci_candidate_failure_review.md`. Hosted CI and branch
protection must pass/be enforced on the next exact candidate before production.

Local verification (2026-10-14, Python 3.13.7): the complete application/root suite
passed 1,098 tests with seven engine-specific skips on Windows (five SQLCipher,
two PostgreSQL), with `-o addopts=`. This is execution evidence, not a coverage
threshold certification. PostgreSQL 16 boundary/migration checks passed 35 tests
without skips, including the real deployment runner's fresh install/downgrade,
injected migration rollback/retry and a non-bypass role. Linux SQLCipher
boundary/adapter/connection checks passed 42 tests without skips, with only the
two PostgreSQL cases deliberately deselected. These synthetic local runs are not
hosted or production-instance certification. Scoped new boundary/gate Ruff and
mypy checks do not certify pre-existing project-wide lint/type issues. The
optional CKEditor bleach warning remains. Exact committed-candidate artifacts,
hosted gates and required branch protection must still be verified before release.

The retention policy is deliberately pending in `docs/audit_retention_policy.json`.
Strict approval validation currently blocks production. Approval must identify
the organization and accountable owners, retention periods, legal holds and
archive/export/backup/deletion runbooks; neither a period nor legal approval has
been invented. Existing history is retained without automatic rewriting/deletion.

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
