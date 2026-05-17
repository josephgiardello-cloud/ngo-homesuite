from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PYTHON = Path(sys.executable)
SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "check_benchmark_regression.py"


def test_benchmark_regression_guard_passes_within_drift(tmp_path):
    baseline = {
        "overall": {"p95_ms": 100.0},
        "endpoints": {"/health": {"p95_ms": 100.0}},
    }
    report = {
        "overall": {"p95_ms": 120.0, "failures": 0},
        "endpoints": {"/health": {"p95_ms": 120.0}},
    }

    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "report.json"
    summary_path = tmp_path / "summary.json"

    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    proc = subprocess.run(
        [
            str(PYTHON),
            str(SCRIPT),
            "--report-json",
            str(report_path),
            "--baseline-json",
            str(baseline_path),
            "--max-p95-drift-pct",
            "25",
            "--summary-json",
            str(summary_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["passed"] is True


def test_benchmark_regression_guard_fails_on_regression(tmp_path):
    baseline = {
        "overall": {"p95_ms": 100.0},
        "endpoints": {"/health": {"p95_ms": 100.0}},
    }
    report = {
        "overall": {"p95_ms": 160.0, "failures": 0},
        "endpoints": {"/health": {"p95_ms": 100.0}},
    }

    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "report.json"

    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    proc = subprocess.run(
        [
            str(PYTHON),
            str(SCRIPT),
            "--report-json",
            str(report_path),
            "--baseline-json",
            str(baseline_path),
            "--max-p95-drift-pct",
            "25",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 2
    assert "exceeds allowed" in proc.stdout
