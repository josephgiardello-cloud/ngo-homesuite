from __future__ import annotations

import sqlite3
from pathlib import Path

from ..auth.session import require_role
from ..config import BACKUP_REMINDER_DAYS
from ..db.utils import audit
from ..prompts import prompt_optional, utc_now_iso
from .backup_core import backup_database_to, backup_reminder_on_startup, default_backup_path, set_app_meta_value


def _backup_reminder_on_startup(*, reminder_days: int | None = None) -> None:
    """Legacy/compat helper expected by the old entry point.

    Uses BACKUP_REMINDER_DAYS by default.
    """

    backup_reminder_on_startup(reminder_days=int(BACKUP_REMINDER_DAYS if reminder_days is None else reminder_days))


@require_role("admin")
def backup_database() -> None:
    """Create a timestamped backup file and record last-backup time."""

    default_path = default_backup_path()
    raw = prompt_optional(f"Backup path (blank for {default_path}): ").strip()
    dest = Path(raw) if raw else default_path

    if dest.exists():
        confirm = prompt_optional(f"Backup file exists, overwrite? {dest} (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
        try:
            dest.unlink()
        except OSError:
            pass

    try:
        backup_database_to(dest)
    except (FileExistsError, OSError, sqlite3.Error, ValueError) as e:
        print(f"Backup failed: {e}")
        return

    set_app_meta_value("last_backup_utc", utc_now_iso())
    print(f"Backup created: {dest}")
    audit("db.backup", entity_type="db", details={"dest": str(dest)})
