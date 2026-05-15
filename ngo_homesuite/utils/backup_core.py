from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import BACKUP_DIRECTORY, DB_PATH
from ..db.connection import connect_db_at, configure_connection, run_db
from ..prompts import parse_utc_iso, utc_now_compact


def get_app_meta_value(key: str) -> str | None:
    def op(_conn: Any, cur: Any):
        cur.execute("SELECT value FROM app_meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return (row[0] if row else None)

    try:
        value = run_db(op)
    except sqlite3.DatabaseError:
        return None
    return str(value) if value is not None else None


def set_app_meta_value(key: str, value: str) -> None:
    def op(_conn: Any, cur: Any) -> None:
        cur.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    run_db(op, write=True)


def default_backup_path() -> Path:
    backup_dir = Path(BACKUP_DIRECTORY)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(DB_PATH).stem
    return backup_dir / f"{stem}_backup_{utc_now_compact()}.db"


def backup_database_to(dest_path: Path) -> None:
    """Create a consistent backup copy of the DB."""

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        raise FileExistsError(str(dest_path))

    dest_str = str(dest_path)

    def op(_conn: Any, _cur: Any) -> None:
        src_conn = connect_db_at(DB_PATH)
        configure_connection(src_conn)
        try:
            dst_conn = connect_db_at(dest_str)
            configure_connection(dst_conn)
            try:
                src_conn.backup(dst_conn)
            finally:
                try:
                    dst_conn.close()
                except sqlite3.Error:
                    pass
        finally:
            try:
                src_conn.close()
            except sqlite3.Error:
                pass

    try:
        run_db(op)
    except (OSError, sqlite3.Error, ValueError):
        if not Path(DB_PATH).exists():
            raise
        shutil.copy2(DB_PATH, dest_path)


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def backup_reminder_on_startup(*, reminder_days: int) -> None:
    last = get_app_meta_value("last_backup_utc")
    if not last:
        print("\nWARNING: No database backup recorded yet. Consider running 'DB Backup'.")
        return
    dt = parse_utc_iso(last)
    if not dt:
        print("\nWARNING: Last backup timestamp is unreadable. Consider creating a new backup.")
        return

    age_days = (_utc_now_dt() - dt).total_seconds() / 86400.0
    if age_days >= float(reminder_days):
        print(f"\nWARNING: Last DB backup is {age_days:.1f} days old. Consider running 'DB Backup'.")
