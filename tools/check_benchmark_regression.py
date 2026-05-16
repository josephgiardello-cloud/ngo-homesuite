from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _threshold(base_value: float, drift_pct: float) -> float:
    return float(base_value) * (1.0 + (drift_pct / 100.0))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail when benchmark metrics regress beyond allowed drift")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--baseline-json", required=True)
    parser.add_argument("--max-p95-drift-pct", type=float, default=35.0)
    parser.add_argument("--summary-json", default="")
    args = parser.parse_args()

    report = _load_json(args.report_json)
    baseline = _load_json(args.baseline_json)

    violations: list[str] = []

    report_overall = report.get("overall", {})
    base_overall = baseline.get("overall", {})

    if int(report_overall.get("failures", 0)) > 0:
        violations.append("overall.failures must be 0")

    report_p95 = float(report_overall.get("p95_ms", 0.0))
    base_p95 = float(base_overall.get("p95_ms", 0.0))
    if base_p95 > 0:
        max_allowed = _threshold(base_p95, args.max_p95_drift_pct)
        if report_p95 > max_allowed:
            violations.append(
                f"overall.p95_ms {report_p95:.1f} exceeds allowed {max_allowed:.1f} (baseline {base_p95:.1f})"
            )

    report_endpoints = report.get("endpoints", {})
    base_endpoints = baseline.get("endpoints", {})

    for endpoint, base_stats in base_endpoints.items():
        if endpoint not in report_endpoints:
            violations.append(f"missing endpoint in report: {endpoint}")
            continue

        endpoint_p95 = float(report_endpoints[endpoint].get("p95_ms", 0.0))
        base_endpoint_p95 = float(base_stats.get("p95_ms", 0.0))
        if base_endpoint_p95 <= 0:
            continue
        endpoint_allowed = _threshold(base_endpoint_p95, args.max_p95_drift_pct)
        if endpoint_p95 > endpoint_allowed:
            violations.append(
                f"{endpoint}.p95_ms {endpoint_p95:.1f} exceeds allowed {endpoint_allowed:.1f} "
                f"(baseline {base_endpoint_p95:.1f})"
            )

    summary = {
        "baseline": args.baseline_json,
        "report": args.report_json,
        "max_p95_drift_pct": args.max_p95_drift_pct,
        "violations": violations,
        "passed": len(violations) == 0,
    }

    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0 if not violations else 2


if __name__ == "__main__":
    raise SystemExit(main())
