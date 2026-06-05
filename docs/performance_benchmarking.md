# Performance and Scalability Benchmarking

Last updated: 2026-06-04

This document defines the baseline benchmark lane for key public endpoints.

## Benchmark Command

```bash
python tools/scalability_benchmark.py \
  --base-url http://127.0.0.1:8765 \
  --endpoint /health \
  --endpoint / \
  --endpoint /give \
  --endpoint /p2p/leaderboard \
  --total-requests 300 \
  --concurrency 20 \
  --max-overall-p95-ms 2200 \
  --report-json artifacts/scalability-benchmark.json
```

## Output

The benchmark emits JSON with:

- overall request count, failures, avg/p95/max latency
- per-endpoint latency stats
- sample failure detail if present

## CI Gate

The CI `scalability-benchmark` job:

- runs the benchmark
- checks regression against `tools/benchmark_baseline.json`
- uploads benchmark and regression-summary JSON artifacts
- release evidence can be attached via `artifacts/scalability-benchmark-local.json` and indexed in `artifacts/release-evidence-bundle.json`

To tune guard strictness, adjust `--max-p95-drift-pct` in workflow config.

## Tuning Guidance

- If p95 exceeds budget:
  - inspect DB pool sizing and timeout settings
  - profile slow query paths for donation and leaderboard endpoints
  - reduce expensive per-request joins or compute-heavy render paths
- Dashboard/reporting summary hot path:
  - `REPORTING_DASHBOARD_CACHE_TTL_SECONDS` (default `15`) enables short-lived in-process caching for `ReportingService.organization_dashboard_summary`
  - set to `0` to disable cache during debugging or deterministic load investigations
  - keep TTL low (10-30s) so dashboards remain fresh while reducing repeated aggregate-query pressure
- Keep benchmark thresholds realistic for your runner class and expected load profile.
