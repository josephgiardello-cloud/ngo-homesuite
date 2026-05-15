from __future__ import annotations

from pathlib import Path

import pytest

from ngo_homesuite.db.migrate import (
    MigrationApplyError,
    MigrationError,
    MigrationPlan,
    auto_migrate,
    plan_migrations,
)
from ngo_homesuite.db import schema as legacy_schema


@pytest.fixture()
def backup_env(monkeypatch):
    monkeypatch.setenv("NGO_HOMESUITE_BACKUP_BEFORE_MIGRATE", "1")
    monkeypatch.setenv("NGO_HOMESUITE_RESTORE_BACKUP_ON_MIGRATION_FAIL", "1")


def _write_migration(path: Path, name: str, sql: str) -> None:
    (path / name).write_text(sql, encoding="utf-8")


def test_auto_migrate_applies_sql_files_and_creates_backup(tmp_path, monkeypatch, backup_env):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    _write_migration(
        migrations_dir,
        "0001_initial.sql",
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS demo_table (
            id INTEGER PRIMARY KEY,
            value TEXT
        );
        """,
    )
    _write_migration(
        migrations_dir,
        "0002_add_flag.sql",
        "ALTER TABLE demo_table ADD COLUMN flag INTEGER DEFAULT 0;",
    )

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)

    db_path = tmp_path / "test.db"
    db_path.write_text("", encoding="utf-8")

    auto_migrate(str(db_path))

    # Re-run is idempotent and should keep backup path available.
    auto_migrate(str(db_path))

    assert (tmp_path / "test.db.bak").exists()


def test_auto_migrate_raises_on_hash_mismatch(tmp_path, monkeypatch, backup_env):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    migration_file = migrations_dir / "0001_initial.sql"
    migration_file.write_text(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS demo_table (id INTEGER PRIMARY KEY);
        """,
        encoding="utf-8",
    )

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)

    db_path = tmp_path / "hash_mismatch.db"
    auto_migrate(str(db_path))

    # Mutate migration contents after initial application to trigger hash validation failure.
    migration_file.write_text(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS demo_table (id INTEGER PRIMARY KEY, note TEXT);
        """,
        encoding="utf-8",
    )

    with pytest.raises(MigrationError):
        auto_migrate(str(db_path))


def test_plan_migrations_reports_pending_versions(tmp_path, monkeypatch):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    _write_migration(
        migrations_dir,
        "0001_initial.sql",
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS t1 (id INTEGER PRIMARY KEY);
        """,
    )
    _write_migration(
        migrations_dir,
        "0002_next.sql",
        "ALTER TABLE t1 ADD COLUMN note TEXT;",
    )

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)

    db_path = tmp_path / "preflight.db"
    plan_before = plan_migrations(str(db_path))
    assert isinstance(plan_before, MigrationPlan)
    assert plan_before.pending_count == 2

    auto_migrate(str(db_path))

    plan_after = plan_migrations(str(db_path))
    assert plan_after.pending_count == 0


def test_plan_migrations_detects_version_gaps(tmp_path, monkeypatch):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    _write_migration(
        migrations_dir,
        "0001_initial.sql",
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        """,
    )
    _write_migration(
        migrations_dir,
        "0003_gap.sql",
        "CREATE TABLE IF NOT EXISTS gap_table (id INTEGER PRIMARY KEY);",
    )

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)

    with pytest.raises(MigrationError):
        plan_migrations(str(tmp_path / "gap.db"))


def test_auto_migrate_wraps_invalid_sql_in_apply_error(tmp_path, monkeypatch):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    _write_migration(
        migrations_dir,
        "0001_initial.sql",
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        """,
    )
    _write_migration(
        migrations_dir,
        "0002_invalid.sql",
        "CREATE TABL malformed_sql_statement;",
    )

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)

    with pytest.raises(MigrationApplyError):
        auto_migrate(str(tmp_path / "broken.db"))


def test_legacy_schema_migrate_requires_explicit_fallback_opt_in(monkeypatch):
    class _FakeCursor:
        def execute(self, _sql):
            return None

        def fetchall(self):
            return []

    class _FakeConn:
        database = "memory"

    monkeypatch.setenv("NGO_HOMESUITE_ALLOW_LEGACY_SCHEMA_FALLBACK", "0")

    import ngo_homesuite.db.migrate as migrate_module

    def _raise_delegate(_db_path=None):
        raise RuntimeError("delegate failed")

    monkeypatch.setattr(migrate_module, "auto_migrate", _raise_delegate)

    with pytest.raises(RuntimeError, match="legacy fallback is disabled"):
        legacy_schema.migrate_schema(_FakeConn(), _FakeCursor())


def test_legacy_schema_migrate_uses_legacy_runner_when_opted_in(monkeypatch):
    class _FakeCursor:
        def execute(self, _sql):
            return None

        def fetchall(self):
            return []

    class _FakeConn:
        database = "memory"

    monkeypatch.setenv("NGO_HOMESUITE_ALLOW_LEGACY_SCHEMA_FALLBACK", "1")

    import ngo_homesuite.db.migrate as migrate_module

    def _raise_delegate(_db_path=None):
        raise RuntimeError("delegate failed")

    monkeypatch.setattr(migrate_module, "auto_migrate", _raise_delegate)

    import ngo_homesuite.db.schema_legacy_fallback as fallback_module

    called = {"used": False}

    def _fake_legacy_runner(_conn, _cur):
        called["used"] = True

    monkeypatch.setattr(fallback_module, "run_legacy_schema_migration", _fake_legacy_runner)

    legacy_schema.migrate_schema(_FakeConn(), _FakeCursor())
    assert called["used"] is True
