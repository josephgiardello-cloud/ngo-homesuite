from __future__ import annotations

import argparse
import sqlite3
import hashlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class MigrationError(RuntimeError):
    """Raised when schema migration validation or execution fails."""


def _resolve_migrations_dir() -> Path:
    try:
        from ngo_homesuite.migrations import MIGRATIONS_DIR
    except ImportError:
        MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
    return Path(MIGRATIONS_DIR)


def _migration_version_from_name(file_path: Path) -> int:
    token = file_path.name.split("_", 1)[0]
    if not token.isdigit():
        raise MigrationError(f"Invalid migration filename: {file_path.name}")
    return int(token)


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            hash TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _load_applied_hashes(conn: sqlite3.Connection) -> dict[int, str]:
    _ensure_schema_version_table(conn)
    rows = conn.execute("SELECT version, hash FROM schema_version ORDER BY version").fetchall()
    return {int(row[0]): str(row[1]) for row in rows}


def plan_migrations(db_path: str | None = None, migrations_dir: Path | None = None) -> dict[str, Any]:
    resolved_db_path = db_path or "ngo_data.db"
    directory = Path(migrations_dir) if migrations_dir is not None else _resolve_migrations_dir()
    migration_files = sorted(directory.glob("*.sql"))
    timeout_s = float(os.environ.get("NGO_HOMESUITE_MIGRATION_TIMEOUT_SEC", "30"))
    conn = sqlite3.connect(resolved_db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        applied = _load_applied_hashes(conn)
        applied_versions = sorted(applied.keys())
        pending: list[dict[str, Any]] = []
        for mf in migration_files:
            version = _migration_version_from_name(mf)
            hash_val = hashlib.sha256(mf.read_bytes()).hexdigest()
            if version in applied:
                if applied[version] != hash_val:
                    raise MigrationError(
                        f"Migration {mf.name} hash mismatch! DB: {applied[version]} File: {hash_val}"
                    )
                continue
            pending.append({"version": version, "name": mf.name, "hash": hash_val})

        expected_next = (max(applied_versions) + 1) if applied_versions else 1
        for migration in pending:
            if migration["version"] != expected_next:
                raise MigrationError(
                    f"Migration gap detected: expected v{expected_next}, found v{migration['version']} ({migration['name']})"
                )
            expected_next += 1

        return {
            "db_path": resolved_db_path,
            "migrations_dir": str(directory),
            "applied_versions": applied_versions,
            "pending": pending,
            "pending_count": len(pending),
        }
    finally:
        conn.close()


def _create_backup_if_needed(db_path: str) -> str | None:
    enabled = os.environ.get("NGO_HOMESUITE_BACKUP_BEFORE_MIGRATE", "1").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    if not db_path or db_path == ":memory:" or db_path.startswith("file:"):
        return None
    source = Path(db_path)
    if not source.exists():
        return None
    backup_path = str(source.with_suffix(source.suffix + ".bak"))
    shutil.copy2(source, backup_path)
    return backup_path


def _restore_backup_if_needed(db_path: str, backup_path: str | None) -> None:
    restore = os.environ.get("NGO_HOMESUITE_RESTORE_BACKUP_ON_MIGRATION_FAIL", "1").lower() in {"1", "true", "yes", "on"}
    if not restore or not backup_path:
        return
    backup = Path(backup_path)
    if not backup.exists():
        return
    if db_path == ":memory:" or db_path.startswith("file:"):
        return
    target = Path(db_path)
    shutil.copy2(backup, target)

def auto_migrate(db_path=None):
    resolved_db_path = db_path or "ngo_data.db"
    migration_plan = plan_migrations(resolved_db_path)
    migration_files = [
        Path(migration_plan["migrations_dir"]) / item["name"]
        for item in migration_plan["pending"]
    ]

    backup_path = _create_backup_if_needed(resolved_db_path)
    timeout_s = float(os.environ.get("NGO_HOMESUITE_MIGRATION_TIMEOUT_SEC", "30"))
    conn = sqlite3.connect(resolved_db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_schema_version_table(conn)
        for mf in migration_files:
            version = _migration_version_from_name(mf)
            with open(mf, 'rb') as f:
                hash_val = hashlib.sha256(f.read()).hexdigest()
            sql = mf.read_text(encoding='utf-8')
            conn.executescript(sql)
            now_utc = datetime.now(UTC).isoformat().replace('+00:00', 'Z')
            conn.execute('INSERT INTO schema_version (version, applied_at_utc, hash) VALUES (?, ?, ?)',
                         (version, now_utc, hash_val))
            # Also update schema_hash table for versioned migrations
            try:
                conn.execute('INSERT INTO schema_hash (version, hash, applied_at_utc) VALUES (?, ?, ?)',
                             (version, hash_val, now_utc))
            except Exception:
                pass  # Table may not exist in early migrations
            conn.commit()
            print(f"Applied migration {version} ({mf.name}) with hash {hash_val}")
        print("All migrations applied and verified.")
    except Exception:
        conn.rollback()
        _restore_backup_if_needed(resolved_db_path, backup_path)
        raise
    finally:
        conn.close()


def run_preflight(db_path: str | None = None, verify_backup: bool = False) -> dict[str, Any]:
    plan = plan_migrations(db_path=db_path)
    if verify_backup:
        backup_path = _create_backup_if_needed(plan["db_path"])
        if backup_path:
            # Restore immediately so this is only a verification step.
            _restore_backup_if_needed(plan["db_path"], backup_path)
            plan["backup_verified"] = True
            plan["backup_path"] = backup_path
        else:
            plan["backup_verified"] = False
            plan["backup_path"] = None
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NGO HomeSuite DB migration runner")
    parser.add_argument("--db-path", default=None, help="Override database path")
    parser.add_argument("--dry-run", action="store_true", help="Validate migration plan without applying migrations")
    parser.add_argument(
        "--verify-backup",
        action="store_true",
        help="With --dry-run, verify backup/restore can execute",
    )
    args = parser.parse_args(argv)

    if args.verify_backup and not args.dry_run:
        parser.error("--verify-backup requires --dry-run")

    if args.dry_run:
        plan = run_preflight(db_path=args.db_path, verify_backup=args.verify_backup)
        print(
            f"Migration preflight: applied={len(plan['applied_versions'])}, pending={plan['pending_count']}, db={plan['db_path']}"
        )
        for pending in plan["pending"]:
            print(f"  - v{pending['version']:04d} {pending['name']}")
        if args.verify_backup:
            print(
                "Backup verification: "
                + ("ok" if plan.get("backup_verified") else "skipped")
            )
        return 0

    auto_migrate(args.db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
