# Release and Versioning Process

Last updated: 2026-06-02

This project uses a lightweight release process to keep rapid changes safe and traceable.

## Versioning

- Use SemVer style tags: `vMAJOR.MINOR.PATCH`.
- `PATCH`: bug fixes and hardening with no intentional behavior break.
- `MINOR`: new features or significant workflow additions.
- `MAJOR`: intentional breaking changes.

## Release Checklist

1. Ensure documentation is current:
- `README.md`
- `docs/feature_status.md`
- `docs/production_checklist.md`
- Relevant ADR updates in `docs/adr/`

2. Validate quality gates:
- Run full tests: `python -m pytest --maxfail=10 -q`
- Run tenant isolation release lane: `python -m pytest ngo_homesuite/web/test_cross_tenant_boundaries.py ngo_homesuite/web/test_rbac_tenant_route_audit.py ngo_homesuite/api/test_v1_runtime.py ngo_homesuite/ai/test_copilot_routes.py ngo_homesuite/ai/test_copilot_tools.py ngo_homesuite/ai/test_semantic_memory.py -v --maxfail=10`
- Run observability release lane: `python -m pytest ngo_homesuite/api/test_observability_api.py ngo_homesuite/api/test_observability_request_logs.py -v --maxfail=10`
- Verify migrations preflight: `python -m ngo_homesuite.db.migrate --dry-run --verify-backup`
- Run backup/restore drill in non-prod: `python -m ngo_homesuite.db.backup_restore_drill --db data/homesuite.db --output-dir backups/drills`
- Run key-rotation drill in non-prod per `docs/key_rotation_drill.md`
- Run DAST smoke probe and archive report: `python tools/dast_smoke.py --base-url http://127.0.0.1:8765 --report-json artifacts/dast-smoke-report.json`
- Run scalability benchmark and archive report: `python tools/scalability_benchmark.py --base-url http://127.0.0.1:8765 --endpoint /health --endpoint / --endpoint /give --endpoint /p2p/leaderboard --total-requests 300 --concurrency 20 --max-overall-p95-ms 2200 --report-json artifacts/scalability-benchmark.json`
- Run HTTP load smoke against release candidate: `python tools/load_test_smoke.py --base-url http://127.0.0.1:8765 --endpoint /health --endpoint / --endpoint /give`
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
- Confirm `.github/workflows/release.yml` completed: tests, OpenAPI validation/client generation, Docker scan, release artifact upload

## Hotfix Policy

- Hotfixes must include at least one regression test when feasible.
- Do not skip full tests unless CI is degraded; if skipped, document risk and run immediately after restore.

## Documentation Integrity Rule

If feature claims change, update `docs/feature_status.md` in the same change set.
