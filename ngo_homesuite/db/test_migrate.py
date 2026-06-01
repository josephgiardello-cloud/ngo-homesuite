from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ngo_homesuite.audit.event_store import verify_workflow_event_immutability_guards
from ngo_homesuite.db.migrate import (
    MigrationApplyError,
    MigrationError,
    MigrationPlan,
    _execute_script_with_retry,
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
    monkeypatch.setenv("LEGACY_FALLBACK_ENABLED", "1")

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


def test_auto_migrate_enforces_backup_policy_when_required(tmp_path, monkeypatch):
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
        CREATE TABLE IF NOT EXISTS demo_table (id INTEGER PRIMARY KEY);
        """,
    )

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setenv("NGO_HOMESUITE_BACKUP_BEFORE_MIGRATE", "0")
    monkeypatch.setenv("NGO_HOMESUITE_REQUIRE_BACKUP_BEFORE_MIGRATE", "1")
    monkeypatch.setenv("NGO_HOMESUITE_MIGRATION_BACKUP_WARN_ONLY", "0")

    db_path = tmp_path / "require_backup.db"
    db_path.write_text("", encoding="utf-8")

    with pytest.raises(MigrationError, match="Backup before migrate is disabled"):
        auto_migrate(str(db_path))


def test_plan_migrations_fails_fast_when_encrypted_db_key_present(tmp_path, monkeypatch):
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

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setenv("NGO_HOMESUITE_DB_KEY", "hex:" + ("aa" * 32))

    with pytest.raises(MigrationError, match="Encrypted database migration is not supported"):
        plan_migrations(str(tmp_path / "encrypted.db"))


def test_plan_migrations_wraps_database_lock_errors(tmp_path, monkeypatch):
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

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setenv("NGO_HOMESUITE_MIGRATION_TIMEOUT_SEC", "0.05")

    db_path = tmp_path / "locked_plan.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS hold_lock (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.execute("BEGIN EXCLUSIVE")

        with pytest.raises(MigrationError, match="lock or access error"):
            plan_migrations(str(db_path))
    finally:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        conn.close()


def test_plan_migrations_handles_larger_pending_sets(tmp_path, monkeypatch):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)

    for version in range(1, 31):
        if version == 1:
            sql = (
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER PRIMARY KEY,"
                "applied_at_utc TEXT NOT NULL,"
                "hash TEXT NOT NULL"
                ");"
            )
        else:
            sql = f"CREATE TABLE IF NOT EXISTS t_{version:04d} (id INTEGER PRIMARY KEY);"
        _write_migration(migrations_dir, f"{version:04d}_m{version}.sql", sql)

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)

    plan = plan_migrations(str(tmp_path / "large_pending.db"))
    assert plan.pending_count == 30
    assert plan.pending[0].version == 1
    assert plan.pending[-1].version == 30


def test_workflow_event_table_is_append_only_after_migrations(tmp_path):
    db_path = tmp_path / "append_only.db"
    db_path.write_text("", encoding="utf-8")

    auto_migrate(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO workflow_events_v2 (
                event_id, org_id, event_type, aggregate_type, aggregate_id, actor_id, payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_test_append_only",
                "org-1",
                "intake_submit",
                "workflow_instance",
                "wf_1",
                "user_1",
                "{}",
                "2026-05-16T00:00:00Z",
            ),
        )
        conn.commit()

        guards = verify_workflow_event_immutability_guards(conn)
        assert guards["ok"] is True

        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(
                "UPDATE workflow_events_v2 SET payload_json=? WHERE event_id=?",
                ("{\"changed\":true}", "evt_test_append_only"),
            )

        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM workflow_events_v2 WHERE event_id=?", ("evt_test_append_only",))
    finally:
        conn.close()


def test_execute_script_with_retry_recovers_after_lock(monkeypatch):
    monkeypatch.setenv("NGO_HOMESUITE_MIGRATION_LOCK_RETRIES", "3")
    monkeypatch.setenv("NGO_HOMESUITE_MIGRATION_LOCK_BACKOFF_SEC", "0")

    class _Conn:
        def __init__(self) -> None:
            self.calls = 0

        def executescript(self, _sql: str) -> None:
            self.calls += 1
            if self.calls == 1:
                raise sqlite3.OperationalError("database is locked")

    conn = _Conn()
    _execute_script_with_retry(conn, "CREATE TABLE IF NOT EXISTS t (id INTEGER);", version=999, filename="lock.sql")
    assert conn.calls == 2


def test_execute_script_with_retry_raises_after_exhaustion(monkeypatch):
    monkeypatch.setenv("NGO_HOMESUITE_MIGRATION_LOCK_RETRIES", "2")
    monkeypatch.setenv("NGO_HOMESUITE_MIGRATION_LOCK_BACKOFF_SEC", "0")

    class _Conn:
        def executescript(self, _sql: str) -> None:
            raise sqlite3.OperationalError("database is locked")

    with pytest.raises(MigrationApplyError, match="database lock"):
        _execute_script_with_retry(_Conn(), "SELECT 1;", version=1000, filename="always_locked.sql")


def test_auto_migrate_fails_fast_when_encrypted_db_key_present(tmp_path, monkeypatch):
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

    import ngo_homesuite.migrations as migrations_module

    monkeypatch.setattr(migrations_module, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setenv("NGO_HOMESUITE_DB_KEY", "hex:" + ("bb" * 32))

    with pytest.raises(MigrationError, match="Encrypted database migration is not supported"):
        auto_migrate(str(tmp_path / "encrypted_auto.db"))
