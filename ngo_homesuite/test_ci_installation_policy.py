from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict:
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_ci_matrix_matches_declared_python_support() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'requires-python = "([^"]+)"', project)
    assert match is not None
    supported = SpecifierSet(match.group(1))
    versions = _workflow("tests.yml")["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    assert versions
    assert all(Version(version) in supported for version in versions)
    assert "3.10" in versions


@pytest.mark.parametrize(
    ("workflow", "job"),
    [("tests.yml", "security"), ("release.yml", "release-verify")],
)
def test_secret_scanner_has_history_for_commit_range(workflow: str, job: str) -> None:
    steps = _workflow(workflow)["jobs"][job]["steps"]
    scanner_index = next(
        index for index, step in enumerate(steps)
        if step.get("uses", "").startswith("gitleaks/gitleaks-action@")
    )
    checkouts = [
        step for step in steps[:scanner_index]
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert checkouts
    assert checkouts[-1].get("with", {}).get("fetch-depth") == "0"


@pytest.mark.parametrize("dockerfile", ["Dockerfile.ci", "Dockerfile.ci-sqlcipher"])
def test_ci_image_installs_existing_manifests_and_accessible_test_tools(dockerfile: str) -> None:
    text = (ROOT / dockerfile).read_text(encoding="utf-8")
    copy_match = re.search(r"^COPY (requirements[^\n]+) /build/$", text, re.MULTILINE)
    assert copy_match is not None
    sources = copy_match.group(1).split()
    assert all((ROOT / source).is_file() for source in sources)
    assert all(
        requirement in sources for requirement in (
            "requirements.txt", "requirements-core.txt", "requirements-db.txt",
            "requirements-ai.txt", "requirements-cloud.txt",
        )
    )
    assert "-r requirements.txt" in text
    assert "COPY --from=builder /opt/venv /opt/venv" in text
    assert "ENV PATH=/opt/venv/bin:$PATH" in text
    assert "/root/.local" not in text
    assert "USER testuser" in text
    for plugin in ("pytest-cov", "pytest-timeout", "pytest-xdist"):
        assert plugin in text
    assert '"ngo_homesuite/"' in text
    assert '"tests/"' in text


def test_encrypted_ci_image_installs_application_sqlcipher_driver() -> None:
    text = (ROOT / "Dockerfile.ci-sqlcipher").read_text(encoding="utf-8")
    assert "sqlcipher3-binary==0.6.0" in text
    assert "NGO_HOMESUITE_REQUIRE_SQLCIPHER_TESTS=1" in text
    assert text.count("FROM python:3.13-slim-bookworm") == 2
    assert "NGO_HOMESUITE_DB_KEY=" not in text


def test_docker_coverage_comes_from_executed_container_and_runtime_key_is_generated() -> None:
    job = _workflow("tests.yml")["jobs"]["docker-matrix-ci"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "secrets.token_hex(32)" in commands
    assert "-e NGO_HOMESUITE_DB_KEY" in commands
    assert "docker create --name extract" not in commands
    assert "docker cp test-run-" in commands
    assert "|| true" not in commands
