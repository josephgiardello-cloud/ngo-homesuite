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
        "ngo_homesuite/tests/test_manual_recurring_and_currency.py",
        "ngo_homesuite/tests/test_financial_audit_privacy.py",
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


def test_release_blocks_missing_dispositions_and_pending_audit_approval() -> None:
    commands = _run_text(_workflow("release.yml")["jobs"]["release-verify"])
    assert "tools/verify_tenant_dispositions.py --inventory artifacts/candidate-tenant-inventory.json --strict" in commands
    assert "tools/verify_audit_retention_policy.py --strict" in commands
    assert _workflow("release.yml")["jobs"]["candidate-database-isolation"]["uses"] == "./.github/workflows/candidate-isolation.yml"


def test_candidate_engine_proofs_are_sha_bound_and_require_real_encryption() -> None:
    job = _workflow("candidate-isolation.yml")["jobs"]["database-isolation"]
    assert job["strategy"]["matrix"]["engine"] == ["postgresql", "sqlite_sqlcipher"]
    commands = _run_text(job)
    assert "REQUIRE_POSTGRES_TENANT_PROOF=1" in commands
    assert "TENANT_TEST_SQLCIPHER=1" in commands
    assert "tools/write_candidate_evidence.py" in commands
    upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert "${{ github.sha }}" in upload["with"]["name"]
    assert upload["with"]["if-no-files-found"] == "error"


def test_secret_lanes_scan_full_history_not_only_candidate_range() -> None:
    for workflow, job in (("tests.yml", "security"), ("release.yml", "release-verify")):
        steps = _workflow(workflow)["jobs"][job]["steps"]
        checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["fetch-depth"] == "0"
        assert any(step.get("uses") == "./.github/actions/full-history-secret-scan" for step in steps)
    action = yaml.safe_load((ROOT / ".github" / "actions" / "full-history-secret-scan" / "action.yml").read_text())
    assert '--log-opts="--all"' in action["runs"]["steps"][0]["run"]
