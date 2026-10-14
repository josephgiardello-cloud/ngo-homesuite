from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict:
    # BaseLoader preserves the GitHub Actions "on" key instead of YAML 1.1 boolean coercion.
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def _run_text(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def test_default_discovery_includes_application_and_root_grant_suites() -> None:
    config = ConfigParser()
    config.read(ROOT / "pytest.ini")
    assert config["pytest"]["testpaths"].split() == ["ngo_homesuite", "tests"]


def test_ci_and_release_full_test_commands_include_both_suites() -> None:
    for workflow, job in (("tests.yml", "test"), ("release.yml", "release-verify")):
        commands = _run_text(_workflow(workflow)["jobs"][job])
        assert "pytest ngo_homesuite/ tests/" in commands


def test_pytest_jobs_install_configured_plugins() -> None:
    for name in ("tests.yml", "release.yml"):
        for job_name, job in _workflow(name)["jobs"].items():
            commands = _run_text(job)
            if "pytest " not in commands:
                continue
            install_lines = [
                line for line in commands.splitlines()
                if "pip install" in line and "pytest" in line
            ]
            assert install_lines, f"{name}:{job_name} does not install pytest tooling"
            assert all("pytest-cov" in line and "pytest-timeout" in line for line in install_lines), (
                f"{name}:{job_name} must install plugins required by pytest.ini"
            )


def test_release_candidates_enforce_existing_dependency_and_reliability_checks() -> None:
    workflow = _workflow("release.yml")
    assert "rc-*" in workflow["on"]["push"]["tags"]
    commands = _run_text(workflow["jobs"]["release-verify"])
    for required in (
        "python tools/check_pyproject_requirements_drift.py",
        "python tools/check_dependency_drift.py",
        "pip-audit -r requirements.txt",
        "ngo_homesuite/tests/test_workflow_reliability_failure_injection.py",
        "ngo_homesuite/persistence/test_workflow_repository_concurrency.py",
        "ngo_homesuite/tests/test_donation_fund_concurrency.py",
        "ngo_homesuite/tests/test_financial_failure_correctness.py",
    ):
        assert required in commands
    assert any(
        step.get("uses", "").startswith("gitleaks/gitleaks-action@")
        for step in workflow["jobs"]["release-verify"]["steps"]
    )


def test_cross_platform_test_job_uses_bash_for_heredocs() -> None:
    job = _workflow("tests.yml")["jobs"]["test"]
    assert "windows-latest" in job["strategy"]["matrix"]["os"]
    assert job["defaults"]["run"]["shell"] == "bash"


def test_release_workflow_has_read_only_default_permissions() -> None:
    assert _workflow("release.yml")["permissions"] == {"contents": "read"}


def test_tenant_gates_include_unassigned_users_and_viewer_write_regressions() -> None:
    for workflow, job in (("tests.yml", "tenant-boundary-gate"), ("release.yml", "release-verify")):
        assert "ngo_homesuite/web/test_unassigned_tenant_boundaries.py" in _run_text(
            _workflow(workflow)["jobs"][job]
        )
