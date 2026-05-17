from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from ..config import DB_ENCRYPTION_KEY_ENV, DB_PATH
from ..prompts import utc_now_compact, utc_now_iso, parse_utc_iso, print_table as _print_table_impl
from ..utils.backup_core import (
    backup_database_to,
    backup_reminder_on_startup,
    default_backup_path,
    get_app_meta_value,
    set_app_meta_value,
)
from .connection import run_db
from ..auth.session import CURRENT_USER

print_table = _print_table_impl


def audit(
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit logging. Never blocks the main operation."""

    if not action or len(action) > 80:
        return

    user_id = None
    username = None
    role = None
    if CURRENT_USER:
        user_id = int(CURRENT_USER["id"]) if CURRENT_USER.get("id") is not None else None
        username = str(CURRENT_USER.get("username") or "") or None
        role = str(CURRENT_USER.get("role") or "") or None

    try:
        details_json = json.dumps(details, ensure_ascii=False, separators=(",", ":")) if details is not None else None
    except (TypeError, ValueError):
        details_json = None

    def op(conn: Any, cur: Any) -> None:
        cur.execute(
            "INSERT INTO audit_log (at_utc, user_id, username, role, action, entity_type, entity_id, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (utc_now_iso(), user_id, username, role, action, entity_type, entity_id, details_json),
        )

    try:
        run_db(op, write=True)
    except Exception:
        return


def show_db_health() -> None:
    def op(_conn: Any, cur: Any):
        cur.execute("SELECT 1")
        ok = cur.fetchone()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in (cur.fetchall() or [])]
        return ok, tables

    try:
        ok_row, tables = run_db(op)
    except sqlite3.Error as e:
        print(f"DB check failed: {e}")
        return

    encryption = "enabled" if os.environ.get(DB_ENCRYPTION_KEY_ENV) else "disabled"
    print("\nDB Health")
    print(f"Path: {DB_PATH}")
    print(f"Encryption: {encryption} ({DB_ENCRYPTION_KEY_ENV} {'set' if encryption == 'enabled' else 'not set'})")
    print(f"Connectivity: {'OK' if ok_row and ok_row[0] == 1 else 'UNKNOWN'}")
    print(f"Tables: {', '.join(tables) if tables else '(none)'}")
