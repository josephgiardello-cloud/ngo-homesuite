from __future__ import annotations

from pathlib import Path

import pytest

from ngo_homesuite.db.migrate import MigrationError, auto_migrate


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
