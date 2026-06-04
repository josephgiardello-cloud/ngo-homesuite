from __future__ import annotations

from pathlib import Path

import pytest

from ngo_homesuite.db import deploy_migrate


class _FakeAppContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeApp:
    def __init__(self, database_uri: str):
        self.config = {"SQLALCHEMY_DATABASE_URI": database_uri}

    def app_context(self):
        return _FakeAppContext()


def test_run_deploy_migration_routes_sqlite_to_legacy_runner(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite3"
    calls: list[str] = []

    monkeypatch.setattr(deploy_migrate, "create_app", lambda: _FakeApp(f"sqlite:///{db_path}"))
    monkeypatch.setattr(deploy_migrate, "auto_migrate", lambda path: calls.append(path))

    result = deploy_migrate.run_deploy_migration("upgrade")

    assert calls == [str(db_path)]
    assert result == {"backend": "sqlite", "action": "upgrade", "revision": "legacy-sql"}


def test_run_deploy_migration_uses_alembic_upgrade_for_non_sqlite(monkeypatch):
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setenv("APP_PROCESS_ROLE", "web")

    monkeypatch.setattr(deploy_migrate, "create_app", lambda: _FakeApp("postgresql://user:pass@db/ngo"))
    monkeypatch.setattr(
        deploy_migrate,
        "alembic_upgrade",
        lambda *, directory, revision: calls.append(("upgrade", directory, revision)),
    )

    result = deploy_migrate.run_deploy_migration("upgrade")

    assert calls == [("upgrade", str(deploy_migrate.ALEMBIC_DIRECTORY), "head")]
    assert result == {"backend": "alembic", "action": "upgrade", "revision": "head"}
    assert deploy_migrate.os.environ["APP_PROCESS_ROLE"] == "web"


def test_run_deploy_migration_uses_alembic_downgrade_for_non_sqlite(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(deploy_migrate, "create_app", lambda: _FakeApp("postgresql://user:pass@db/ngo"))
    monkeypatch.setattr(
        deploy_migrate,
        "alembic_downgrade",
        lambda *, directory, revision: calls.append(("downgrade", directory, revision)),
    )

    result = deploy_migrate.run_deploy_migration("downgrade", revision="base")

    assert calls == [("downgrade", str(deploy_migrate.ALEMBIC_DIRECTORY), "base")]
    assert result == {"backend": "alembic", "action": "downgrade", "revision": "base"}


def test_run_deploy_migration_rejects_sqlite_downgrade(monkeypatch):
    monkeypatch.setattr(deploy_migrate, "create_app", lambda: _FakeApp("sqlite:///data/app.sqlite3"))

    with pytest.raises(RuntimeError, match="only supports upgrade"):
        deploy_migrate.run_deploy_migration("downgrade")