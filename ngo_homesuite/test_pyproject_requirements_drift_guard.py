from __future__ import annotations

import pytest

from tools.check_pyproject_requirements_drift import (
    check_drift,
    extract_dependencies,
    extract_pyproject_deps,
)


def test_includes_are_relative_and_extras_do_not_truncate_toml(tmp_path) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (tmp_path / "requirements.txt").write_text("-r profiles/core.txt\n", encoding="utf-8")
    (profiles / "core.txt").write_text(
        "psycopg[binary]==3.2.9\n--requirement db.txt\n", encoding="utf-8"
    )
    (profiles / "db.txt").write_text("Flask_SQLAlchemy==3.1.1 # database\n", encoding="utf-8")
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\ndependencies = ["psycopg[binary]==3.2.9", "Flask-SQLAlchemy==3.1.1"]\n',
        encoding="utf-8",
    )
    assert set(extract_pyproject_deps(project)) == {"psycopg", "flask-sqlalchemy"}
    assert not check_drift(project, tmp_path / "requirements.txt")


@pytest.mark.parametrize("bundle", [
    "Flask==3.0.0\n", "Flask==3.1.2\nExtra==1\n", "",
])
def test_missing_extra_and_version_drift_fail(tmp_path, bundle) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\ndependencies = ["Flask==3.1.2"]\n', encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(bundle, encoding="utf-8")
    assert check_drift(project, requirements)


def test_cyclic_includes_fail_explicitly(tmp_path) -> None:
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("-r second.txt\n", encoding="utf-8")
    second.write_text("--requirement=first.txt\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Cyclic"):
        extract_dependencies(first)


def test_conflicting_pins_fail_explicitly(tmp_path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("Flask==3.1.2\nFlask==3.0.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Conflicting"):
        extract_dependencies(requirements)
