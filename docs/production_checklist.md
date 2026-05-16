# Production Checklist

Last updated: 2026-05-16

Use this checklist before production deployment.

## Security and Secrets

- Set explicit `SECRET_KEY` and `DATABASE_URL` in production.
- For Docker Compose, set a strong `POSTGRES_PASSWORD` (do not use placeholders/defaults).
- If using SQLCipher, store `NGO_HOMESUITE_DB_KEY` in a secret manager.
- Document key recovery and rotation steps.
- Disable any emergency fallback flags after incident recovery.

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

## UX and Workflow Validation

- Test public forms on mobile breakpoints.
- Validate donor-facing copy, receipts, and redirect paths.
- Run an operator walkthrough for core flows: donor intake, donation, P2P campaign, reporting export.

## Release Gate

A release is production-ready only when:
- All required checklist items are complete.
- Open high-severity issues are resolved or explicitly accepted.
- Feature maturity labels are current in `docs/feature_status.md`.
