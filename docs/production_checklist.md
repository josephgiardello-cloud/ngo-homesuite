# Production Checklist

Last updated: 2026-05-15

Use this checklist before production deployment.

## Security and Secrets

- Set explicit `SECRET_KEY` and `DATABASE_URL` in production.
- If using SQLCipher, store `NGO_HOMESUITE_DB_KEY` in a secret manager.
- Document key recovery and rotation steps.
- Disable any emergency fallback flags after incident recovery.

## Database and Migrations

- Run migration preflight: `python -m ngo_homesuite.db.migrate --dry-run --verify-backup`.
- Confirm backup policy is active before migration.
- Validate restore path from latest backup in a non-prod environment.
- For higher concurrency workloads, evaluate PostgreSQL migration path before scale-up.

## Multi-Tenant Safety

- Verify org scoping on all list/search/report endpoints.
- Verify AI/RAG retrieval context is tenant-scoped.
- Run cross-tenant negative tests for mutating endpoints.

## Reliability and Operations

- Run full test suite in CI on release branch.
- Add smoke tests for public donation and P2P paths.
- Configure process supervisor with restart policy and liveness checks.
- Confirm backup job schedule and retention policy.

## Observability

- Forward application logs to centralized storage (for example ELK/Azure Monitor).
- Expose and scrape metrics endpoint for alerting.
- Alert on migration failures, repeated DB lock errors, and key integrity checks.

## UX and Workflow Validation

- Test public forms on mobile breakpoints.
- Validate donor-facing copy, receipts, and redirect paths.
- Run an operator walkthrough for core flows: donor intake, donation, P2P campaign, reporting export.

## Release Gate

A release is production-ready only when:
- All required checklist items are complete.
- Open high-severity issues are resolved or explicitly accepted.
- Feature maturity labels are current in `docs/feature_status.md`.
