import multiprocessing
import os
import sqlite3
from pathlib import Path

import pytest

from backend.app.worker import SQLiteEventSink
from ngo_homesuite.db import schema
from ngo_homesuite.domain.outbox import deliver_pending
from ngo_homesuite.domain.services import Actor, Services
from ngo_homesuite.domain.store import TenantStore


def crash_business(path, user_id, donor_id, stage):
    class Crashing(Services):
        @staticmethod
        def event(conn, actor, action, entity, entity_id, details):
            def crash_before_statement(statement):
                sql = statement.strip().upper()
                if (
                    (stage == "after_state" and sql.startswith("INSERT INTO AUDIT_LOG"))
                    or (stage == "after_audit" and sql.startswith("INSERT INTO OUTBOX"))
                    or (stage == "after_event" and sql == "COMMIT")
                ):
                    os._exit(91)

            conn.set_trace_callback(crash_before_statement)
            Services.event(conn, actor, action, entity, entity_id, details)

    service = Crashing(TenantStore({"alpha": path}))
    service.record_donation(Actor("alpha", user_id), donor_id, "20", "crash")
    os._exit(91)


def crash_delivery(path, sink):
    receiver = SQLiteEventSink(Path(sink))

    def receive(key, payload):
        receiver(key, payload)
        os._exit(92)

    deliver_pending(TenantStore({"alpha": path}), "alpha", receive, limit=1)


def crash_migration(path):
    original = schema._services

    def fail(conn):
        original(conn)
        os._exit(93)

    schema._services = fail
    schema.init_db(str(path))


def run_child(target, *args):
    process = multiprocessing.get_context("spawn").Process(target=target, args=args)
    process.start()
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("Crash test subprocess hung")
    return process.exitcode


@pytest.mark.parametrize("stage", ["after_state", "after_audit", "after_event", "after_commit"])
def test_process_crashes_preserve_atomicity(system, stage):
    donor = system.services.create_donor(system.admin, "Crash donor")
    assert run_child(crash_business, system.paths["alpha"], system.admin.user_id, donor, stage) == 91
    with system.store.transaction("alpha") as conn:
        donations = conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='donation.create'").fetchone()[0]
        assert (donations, events) == ((1, 1) if stage == "after_commit" else (0, 0))
        assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == conn.execute(
            "SELECT COUNT(*) FROM audit_log",
        ).fetchone()[0]
    # A retry after either rollback or commit produces exactly one receipt.
    system.services.record_donation(system.admin, donor, "20", "crash")
    assert len(system.services.list_entities(system.admin, "donations")) == 1


def test_delivery_crash_and_retry_deduplicated(system, tmp_path):
    sink = tmp_path / "sink.db"
    with sqlite3.connect(sink) as conn:
        conn.execute("CREATE TABLE received(event_key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    assert run_child(crash_delivery, system.paths["alpha"], sink) == 92
    with system.store.transaction("alpha") as conn:
        row = conn.execute("SELECT * FROM outbox ORDER BY id LIMIT 1").fetchone()
        assert row["delivered_at"] is None and row["attempts"] == 1

    def receive(key, payload):
        with sqlite3.connect(sink) as conn:
            conn.execute("INSERT OR IGNORE INTO received VALUES (?, ?)", (key, payload))

    assert deliver_pending(system.store, "alpha", receive, limit=1) == 1
    with sqlite3.connect(sink) as conn:
        assert conn.execute("SELECT COUNT(*) FROM received").fetchone()[0] == 1
    with system.store.transaction("alpha") as conn:
        row = conn.execute("SELECT * FROM outbox ORDER BY id LIMIT 1").fetchone()
        assert row["delivered_at"] is not None and row["attempts"] == 2


def test_delivery_error_is_durable_and_explicit(system):
    def fail(_key, _payload):
        raise ConnectionError("sink unavailable")

    with pytest.raises(ConnectionError, match="sink unavailable"):
        deliver_pending(system.store, "alpha", fail, limit=1)
    with system.store.transaction("alpha") as conn:
        row = conn.execute("SELECT * FROM outbox ORDER BY id LIMIT 1").fetchone()
        assert row["delivered_at"] is None and row["last_error"] == "sink unavailable"


@pytest.mark.parametrize("previous", ["v0.sql", "v1.sql"])
def test_migration_process_crash_recovers_previous_schema(tmp_path, previous):
    path = tmp_path / "migration-crash.db"
    fixture = Path(__file__).parent / "fixtures" / previous
    with sqlite3.connect(path) as conn:
        conn.executescript(fixture.read_text())
        before = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    assert run_child(crash_migration, path) == 93
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall() == before
        assert conn.execute("SELECT amount FROM donations").fetchone()[0] == 12.34
    schema.init_db(str(path))
