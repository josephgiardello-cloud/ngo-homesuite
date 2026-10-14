from pathlib import Path
import sqlite3

import pytest

from ngo_homesuite.db import schema

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("previous", [None, "v0.sql", "v1.sql"])
def test_fresh_and_supported_upgrades(tmp_path, previous):
    path = tmp_path / "upgrade.db"
    if previous:
        with sqlite3.connect(path) as conn:
            conn.executescript((FIXTURES / previous).read_text())
    schema.init_db(str(path))
    schema.init_db(str(path))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone() == ("2",)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(schema.TABLES) | {"grants", "outbox", "web_sessions", "login_attempts"} <= tables
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        if previous:
            assert conn.execute("SELECT amount, purpose FROM donations WHERE id=1").fetchone() == (
                12.34, "Historical receipt",
            )
        if previous == "v0.sql":
            assert conn.execute("SELECT status FROM payroll_runs").fetchone() == ("paid",)
            assert conn.execute("SELECT jurisdiction_code FROM staff").fetchone() == ("US-ME",)


@pytest.mark.parametrize("previous", [None, "v0.sql", "v1.sql"])
def test_migration_failure_rolls_back_and_recovers(tmp_path, monkeypatch, previous):
    path = tmp_path / "upgrade.db"
    with sqlite3.connect(path) as conn:
        if previous:
            conn.executescript((FIXTURES / previous).read_text())
        before = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    original = schema._services

    def fail(conn):
        original(conn)
        raise sqlite3.OperationalError("Injected failure after schema and version-1 changes")

    monkeypatch.setattr(schema, "_services", fail)
    with pytest.raises(sqlite3.OperationalError, match="Injected"):
        schema.init_db(str(path))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall() == before
    monkeypatch.setattr(schema, "_services", original)
    schema.init_db(str(path))


def test_duplicate_legacy_email_requires_explicit_recovery(tmp_path):
    path = tmp_path / "duplicate.db"
    with sqlite3.connect(path) as conn:
        conn.executescript((FIXTURES / "v0.sql").read_text())
        conn.execute("INSERT INTO donors(id, name, email) VALUES (2, 'Duplicate', 'historic@example.org')")
    with pytest.raises(sqlite3.IntegrityError):
        schema.init_db(str(path))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM donors").fetchone()[0] == 2
        conn.execute("UPDATE donors SET email = NULL WHERE id = 2")
    schema.init_db(str(path))


def test_future_schema_rejected_without_changes(tmp_path):
    path = tmp_path / "future.db"
    schema.init_db(str(path))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE app_meta SET value='999' WHERE key='schema_version'")
    with pytest.raises(ValueError, match="Unsupported"):
        schema.init_db(str(path))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0] == "999"
