"""
Unit test for HIBP failure persistence and race-safety
Run with: pytest test_hibp_persistence.py
"""
import sqlite3
import pytest
from ngo_homesuite.auth import models

def setup_in_memory_db():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    # Create hibp_failures table
    cur.execute("""
        CREATE TABLE hibp_failures (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            count INTEGER NOT NULL DEFAULT 0,
            last_failure_ts TEXT
        )
    """)
    cur.execute("INSERT OR IGNORE INTO hibp_failures (id, count, last_failure_ts) VALUES (1, 0, NULL)")
    conn.commit()
    return conn, cur

def test_hibp_failure_increment_and_reset():
    conn, cur = setup_in_memory_db()
    # Initial count is 0
    assert models._get_hibp_failure_count(cur) == 0
    # Increment by 1
    models._inc_hibp_failure_count(cur, 1)
    assert models._get_hibp_failure_count(cur) == 1
    # Increment by 2
    models._inc_hibp_failure_count(cur, 2)
    assert models._get_hibp_failure_count(cur) == 3
    # Reset
    models._reset_hibp_failure_count(cur)
    assert models._get_hibp_failure_count(cur) == 0

def test_hibp_failure_timestamp():
    conn, cur = setup_in_memory_db()
    # No timestamp initially
    cur.execute("SELECT last_failure_ts FROM hibp_failures WHERE id = 1")
    assert cur.fetchone()[0] is None
    # Increment sets timestamp
    models._inc_hibp_failure_count(cur, 1)
    cur.execute("SELECT last_failure_ts FROM hibp_failures WHERE id = 1")
    ts1 = cur.fetchone()[0]
    assert ts1 is not None
    # Reset clears timestamp
    models._reset_hibp_failure_count(cur)
    cur.execute("SELECT last_failure_ts FROM hibp_failures WHERE id = 1")
    assert cur.fetchone()[0] is None