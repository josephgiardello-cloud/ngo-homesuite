from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_dependency_drift_guard_script_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "tools" / "check_dependency_drift.py"

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
