from __future__ import annotations

import sqlite3
from pathlib import Path


def test_email_queue_migration_sql_executes():
    migration = Path(__file__).resolve().parents[1] / "migrations" / "0021_create_email_queue.sql"
    sql = migration.read_text(encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(sql)
        table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='email_queue'").fetchone()
        index = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_email_queue_status'").fetchone()
        assert table is not None
        assert index is not None
    finally:
        conn.close()
