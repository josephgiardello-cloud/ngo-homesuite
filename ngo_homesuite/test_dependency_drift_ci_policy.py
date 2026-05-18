from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_ci_workflow_enforces_dependency_drift_guards() -> None:
    workflow_text = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")

    assert "python tools/check_pyproject_requirements_drift.py" in workflow_text
    assert "python tools/check_dependency_drift.py" in workflow_text
