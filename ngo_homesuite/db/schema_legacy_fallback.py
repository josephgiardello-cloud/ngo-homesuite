from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import sys
import time
import traceback
from typing import Any


def _migration_lock_host() -> str:
    uname_fn = getattr(os, "uname", None)
    if callable(uname_fn):
        try:
            host_info = uname_fn()
            return str(getattr(host_info, "nodename", "unknown"))
        except Exception:
            pass
    return os.environ.get("COMPUTERNAME", "unknown")


def run_legacy_schema_migration(conn: Any, cur: Any) -> None:
    """Execute the frozen legacy schema migration flow for emergency recovery only."""
    from ngo_homesuite.db import schema as schema_module

    def backup_db_file() -> str | None:
        db_path = getattr(conn, "database", None)
        if not db_path or db_path in (":memory:", ""):
            print("[BACKUP] Skipping backup: in-memory or unknown DB.", file=sys.stderr)
            return None
        backup_path = db_path + ".bak"
        try:
            shutil.copy2(db_path, backup_path)
            print(f"[BACKUP] Database backed up to {backup_path}", file=sys.stdout)
            return backup_path
        except Exception as exc:
            print(f"[BACKUP] Backup failed: {exc}", file=sys.stderr)
            return None

    backup_path = backup_db_file()
    schema_module.migration_configure_connection(conn, cur)

    lock_acquired = False
    max_wait = 30
    wait_interval = 1
    waited = 0
    lock_id = 1
    lock_owner = f"pid:{os.getpid()}@{_migration_lock_host()}"

    while not lock_acquired and waited < max_wait:
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "INSERT OR IGNORE INTO migration_lock (id, locked_at, locked_by) VALUES (?, NULL, NULL)",
                (lock_id,),
            )
            cur.execute("SELECT locked_at, locked_by FROM migration_lock WHERE id = ?", (lock_id,))
            row = cur.fetchone()
            if row and row[0] is None:
                now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cur.execute(
                    "UPDATE migration_lock SET locked_at = ?, locked_by = ? WHERE id = ?",
                    (now_utc, lock_owner, lock_id),
                )
                lock_acquired = True
                conn.commit()
            else:
                conn.rollback()
                print(
                    f"[MIGRATION LOCK] Another migration is in progress by {row[1]} since {row[0]}. Waiting...",
                    file=sys.stderr,
                )
                time.sleep(wait_interval)
                waited += wait_interval
        except sqlite3.OperationalError as exc:
            conn.rollback()
            print(f"[MIGRATION LOCK] DB busy or locked: {exc}. Retrying...", file=sys.stderr)
            time.sleep(wait_interval)
            waited += wait_interval

    try:
        cur.execute("BEGIN IMMEDIATE TRANSACTION")
        cur.execute("PRAGMA foreign_keys = ON;")
        version = schema_module.get_current_schema_version(cur)

        for migration_version in schema_module.MIGRATION_VERSIONS:
            if migration_version <= version:
                continue

            expected_version = version + 1
            if migration_version != expected_version:
                raise RuntimeError(
                    f"Migration gap or out-of-order migration: expected v{expected_version}, got v{migration_version}"
                )
            if migration_version not in schema_module.MIGRATIONS:
                raise ValueError(f"No migration for v{migration_version}")

            if version > 0:
                cur.execute("SELECT schema_hash FROM schema_version WHERE version = ?", (version,))
                expected_hash = cur.fetchone()
                cur.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%'"
                )
                items = sorted(cur.fetchall())
                current_schema_sql = "\n".join([row[1] for row in items if row[1]])
                current_schema_hash = schema_module.compute_schema_hash(current_schema_sql)
                if expected_hash and expected_hash[0] != current_schema_hash:
                    raise RuntimeError(
                        f"Schema drift detected before migration v{migration_version}: "
                        f"expected {expected_hash[0]}, got {current_schema_hash}"
                    )

            schema_module.MIGRATIONS[migration_version](conn, cur)

            cur.execute(
                "SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%'"
            )
            items = sorted(cur.fetchall())
            schema_sql = "\n".join([row[1] for row in items if row[1]])
            schema_hash = schema_module.compute_schema_hash(schema_sql)
            description = (
                "Initial fundraising schema with donor lists"
                if migration_version == 1
                else f"Migrating to v{migration_version}"
            )
            cur.execute(
                "INSERT INTO schema_version (version, description, schema_hash) VALUES (?, ?, ?)",
                (migration_version, description, schema_hash),
            )

            cur.execute("SELECT schema_hash FROM schema_version WHERE version = ?", (migration_version,))
            expected_hash_post = cur.fetchone()
            if expected_hash_post and expected_hash_post[0] != schema_hash:
                raise RuntimeError(
                    f"Schema hash mismatch after migration v{migration_version}: "
                    f"expected {expected_hash_post[0]}, got {schema_hash}"
                )
            version = migration_version

        cur.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%'"
        )
        items = sorted(cur.fetchall())
        final_schema_sql = "\n".join([row[1] for row in items if row[1]])
        final_schema_hash = schema_module.compute_schema_hash(final_schema_sql)
        cur.execute("UPDATE schema_version SET schema_hash = ? WHERE version = ?", (final_schema_hash, version))

        expected_tables = {
            "schema_version",
            "staff",
            "bank_accounts",
            "donors",
            "donor_lists",
            "donor_list_members",
            "allowed_currencies",
            "active_staff",
            "active_donors",
            "migration_lock",
            "migration_log",
            "__db_metadata__",
        }
        if version >= 2:
            expected_tables.update(["funds", "projects", "donations", "expenses", "audit_log"])
        if version >= 4:
            expected_tables.update(
                [
                    "campaigns",
                    "pledges",
                    "interactions",
                    "households",
                    "donor_relationships",
                    "grants",
                    "active_campaigns",
                    "active_pledges",
                    "active_interactions",
                    "active_households",
                    "active_grants",
                    "active_donor_relationships",
                ]
            )
        if version >= 9:
            expected_tables.update(["events", "registrations", "active_events", "event_registrations"])
        if version >= 11:
            expected_tables.update(["volunteers", "volunteer_assignments", "active_volunteers"])
        if version >= 12:
            expected_tables.update(["peer_fundraising_pages", "peer_fundraising_donations"])

        schema_module.detect_schema_drift(cur, expected_tables, "POST")

        try:
            cur.execute("PRAGMA optimize;")
            print("[MIGRATION] PRAGMA optimize executed.", file=sys.stdout)
        except Exception as exc:
            print(f"[MIGRATION] PRAGMA optimize failed: {type(exc).__name__}: {exc}", file=sys.stderr)

        conn.commit()
        cur.execute("UPDATE migration_lock SET locked_at = NULL, locked_by = NULL WHERE id = ?", (lock_id,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"FATAL: Migration failed. {type(exc).__name__}: {exc}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        if backup_path:
            try:
                schema_module.log_migration_event(
                    cur,
                    -1,
                    "ROLLBACK",
                    f"Attempting rollback from backup: {backup_path}",
                )
                schema_module.rollback_schema(conn, cur, backup_path)
                print(f"[ROLLBACK] Rollback from backup {backup_path} completed.", file=sys.stdout)
            except Exception as rollback_exc:
                print(f"[ROLLBACK] Rollback failed: {rollback_exc}", file=sys.stderr)
        else:
            print("[ROLLBACK] No backup available for rollback.", file=sys.stderr)

        try:
            cur.execute("UPDATE migration_lock SET locked_at = NULL, locked_by = NULL WHERE id = ?", (lock_id,))
            conn.commit()
        except Exception:
            pass