# NGO HomeSuite Phase 2 Roadmap

This roadmap tracks hardening and productionization after the V2 baseline.

## 1. Config Standardization
- Complete centralized settings adoption for remaining modules still reading env vars directly.
- Add startup config diagnostics endpoint (admin-only) showing sanitized effective config.
- Add schema-level validation for optional YAML profiles.

## 2. Packaging and Supply Chain
- Generate and commit `requirements.lock` with hashes.
- Add CI guard to fail when `requirements.txt` and `pyproject.toml` drift.
- Add Dependabot or Renovate policy for controlled dependency updates.

## 3. Encryption and Migration Reliability
- Expand SQLCipher key rotation test matrix (success, bad-old-key, concurrent lock conditions).
- Add migration chaos tests with intentional lock contention.
- Add operator runbook for rollback and incident response.

## 4. RBAC and Tenant Isolation
- Complete route-by-route RBAC audit checklist.
- Add tenant boundary tests for all workflow and minion endpoints.
- Add secure first-admin bootstrap flow (one-time setup token pattern).

## 5. Minion Reliability
- Add model health probes with circuit-breaker behavior.
- Add retry/backoff profile for transient Ollama errors.
- Add retention policy tests for conversation cleanup behavior.

## 6. Testing and CI
- Add integration journey tests (donor -> donation -> workflow -> report).
- Add Docker matrix jobs: plaintext SQLite and encrypted mode.
- Add coverage thresholds per package (`api`, `workflow_engine`, `observability`).

## 7. Security Hardening
- Add stricter production CSP profile and nonce strategy.
- Extend security audit events (auth failures, approval replay attempts, policy denials).
- Add session cookie hardening verification tests for production config.

## 8. UI Consistency
- Standardize shared template blocks/components.
- Add mobile regression snapshots for donor/workflow/minion pages.
- Ensure CSRF protection coverage on all mutable form routes.

## 9. Error UX
- Add user-facing retry guidance for long-running AI operations.
- Add structured troubleshooting pages for startup/config/migration failures.

## 10. Docs and Onboarding
- Keep command snippets aligned to `python -m ngo_homesuite.main`.
- Keep architecture diagrams current with deployed route topology.
- Add local dev setup script + pre-commit bootstrap helper.

