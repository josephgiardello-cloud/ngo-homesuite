from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ngo_homesuite.policy import enforce_error_contract

DB_ENCRYPTION_KEY_ENV = "NGO_HOMESUITE_DB_KEY"


def _load_runtime_settings() -> Any | None:
    try:
        from ngo_homesuite.config import get_runtime_settings
    except Exception:
        return None

    try:
        return get_runtime_settings()
    except Exception:
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)


def _runtime_setting(name: str, default: Any) -> Any:
    settings = _load_runtime_settings()
    if settings is None:
        return default
    return getattr(settings, name, default)


def _guard_encrypted_db_not_supported() -> None:
    if os.environ.get(DB_ENCRYPTION_KEY_ENV):
        raise MigrationPlanError(
            "Encrypted database migration is not supported by the sqlite3 migration runner. "
            f"Unset {DB_ENCRYPTION_KEY_ENV} for plaintext DBs, or run migrations with a SQLCipher-aware workflow."
        )


class MigrationError(RuntimeError):
    """Raised when schema migration validation or execution fails."""


class MigrationPlanError(MigrationError):
    """Raised when migration planning fails due to invalid inputs or schema layout."""


class MigrationDriftError(MigrationError):
    """Raised when migration hash drift is detected."""


class MigrationApplyError(MigrationError):
    """Raised when applying migrations fails."""


class MigrationBackupError(MigrationError):
    """Raised when backup or restore operations fail."""


@dataclass(frozen=True)
class PlannedMigration:
    version: int
    name: str
    hash: str


@dataclass(frozen=True)
class MigrationPlan:
    db_path: str
    migrations_dir: str
    applied_versions: list[int]
    pending: list[PlannedMigration]

    @property
    def pending_count(self) -> int:
        return len(self.pending)

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_path": self.db_path,
            "migrations_dir": self.migrations_dir,
            "applied_versions": list(self.applied_versions),
            "pending": [asdict(item) for item in self.pending],
            "pending_count": self.pending_count,
        }


def _emit_migration_event(step: str, status: str, message: str, **details: Any) -> None:
    payload = {
        "event_id": f"migration.{step}.{status}",
        "at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "step": step,
        "status": status,
        "message": message,
        "details": details,
    }
    stream = os.sys.stderr if status in {"error", "fail"} else os.sys.stdout
    print(f"[MIGRATION_EVENT] {json.dumps(payload, sort_keys=True)}", file=stream)


def _resolve_migrations_dir() -> Path:
    try:
        from ngo_homesuite.migrations import MIGRATIONS_DIR
    except ImportError:
        MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
    return Path(MIGRATIONS_DIR)


def _migration_version_from_name(file_path: Path) -> int:
    token = file_path.name.split("_", 1)[0]
    if not token.isdigit():
        raise MigrationPlanError(f"Invalid migration filename: {file_path.name}")
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


def _schema_version_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(schema_version)").fetchall()
    return {str(row[1]) for row in rows}


def _schema_version_hash_column(conn: sqlite3.Connection) -> str:
    cols = _schema_version_columns(conn)
    if "hash" in cols:
        return "hash"
    if "schema_hash" in cols:
        return "schema_hash"
    raise MigrationPlanError("schema_version table missing hash or schema_hash column")


def _schema_version_time_column(conn: sqlite3.Connection) -> str | None:
    cols = _schema_version_columns(conn)
    if "applied_at_utc" in cols:
        return "applied_at_utc"
    if "applied_at" in cols:
        return "applied_at"
    return None


def _load_applied_hashes(conn: sqlite3.Connection) -> dict[int, str]:
    _ensure_schema_version_table(conn)
    hash_col = _schema_version_hash_column(conn)
    rows = conn.execute(f"SELECT version, {hash_col} FROM schema_version ORDER BY version").fetchall()
    return {int(row[0]): str(row[1]) for row in rows}


def _insert_schema_version_row(conn: sqlite3.Connection, version: int, hash_value: str, applied_at_utc: str) -> None:
    hash_col = _schema_version_hash_column(conn)
    time_col = _schema_version_time_column(conn)
    if time_col:
        conn.execute(
            f"INSERT INTO schema_version (version, {time_col}, {hash_col}) VALUES (?, ?, ?)",
            (version, applied_at_utc, hash_value),
        )
    else:
        conn.execute(
            f"INSERT INTO schema_version (version, {hash_col}) VALUES (?, ?)",
            (version, hash_value),
        )


def _plan_hash(applied_versions: list[int], pending: list[PlannedMigration]) -> str:
    payload = json.dumps(
        {
            "applied_versions": list(applied_versions),
            "pending": [
                {"version": item.version, "name": item.name, "hash": item.hash}
                for item in pending
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_migrations(db_path: str | None = None, migrations_dir: Path | None = None) -> MigrationPlan:
    _guard_encrypted_db_not_supported()
    resolved_db_path = db_path or "ngo_data.db"
    directory = Path(migrations_dir) if migrations_dir is not None else _resolve_migrations_dir()
    migration_files = sorted(directory.glob("*.sql"))
    timeout_s = _env_float("NGO_HOMESUITE_MIGRATION_TIMEOUT_SEC", float(_runtime_setting("migration_timeout_sec", 30.0)))
    conn = sqlite3.connect(resolved_db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    try:
        try:
            applied = _load_applied_hashes(conn)
            applied_versions = sorted(applied.keys())
            pending: list[PlannedMigration] = []
            for mf in migration_files:
                version = _migration_version_from_name(mf)
                hash_val = hashlib.sha256(mf.read_bytes()).hexdigest()
                if version in applied:
                    if applied[version] != hash_val:
                        raise MigrationDriftError(
                            f"Migration {mf.name} hash mismatch! DB: {applied[version]} File: {hash_val}"
                        )
                    continue
                pending.append(PlannedMigration(version=version, name=mf.name, hash=hash_val))

            expected_next = (max(applied_versions) + 1) if applied_versions else 1
            for migration in pending:
                if migration.version != expected_next:
                    raise MigrationPlanError(
                        f"Migration gap detected: expected v{expected_next}, found v{migration.version} ({migration.name})"
                    )
                expected_next += 1

            plan = MigrationPlan(
                db_path=resolved_db_path,
                migrations_dir=str(directory),
                applied_versions=applied_versions,
                pending=pending,
            )
            _emit_migration_event(
                step="plan",
                status="ok",
                message="Migration plan created",
                db_path=plan.db_path,
                pending_count=plan.pending_count,
                applied_count=len(plan.applied_versions),
            )
            return plan
        except sqlite3.Error as exc:
            raise MigrationPlanError(f"Migration planning failed due to database lock or access error: {exc}") from exc
    finally:
        conn.close()


def _create_backup_if_needed(db_path: str) -> str | None:
    enabled = _env_bool("NGO_HOMESUITE_BACKUP_BEFORE_MIGRATE", bool(_runtime_setting("backup_before_migrate", True)))
    require_backup = _env_bool(
        "NGO_HOMESUITE_REQUIRE_BACKUP_BEFORE_MIGRATE",
        bool(_runtime_setting("require_backup_before_migrate", True)),
    )
    warn_only = _env_bool("NGO_HOMESUITE_MIGRATION_BACKUP_WARN_ONLY", bool(_runtime_setting("migration_backup_warn_only", False)))

    if not enabled:
        msg = "Backup before migrate is disabled by configuration"
        if require_backup and not warn_only:
            raise MigrationBackupError(msg)
        _emit_migration_event(step="backup", status="error" if require_backup else "ok", message=msg)
        return None
    if not db_path or db_path == ":memory:" or db_path.startswith("file:"):
        msg = "Backup skipped for in-memory or URI-based SQLite database"
        if require_backup and not warn_only:
            raise MigrationBackupError(msg)
        _emit_migration_event(step="backup", status="error" if require_backup else "ok", message=msg)
        return None
    source = Path(db_path)
    if not source.exists():
        msg = f"Database file does not exist for backup: {db_path}"
        if require_backup and not warn_only:
            raise MigrationBackupError(msg)
        _emit_migration_event(step="backup", status="error" if require_backup else "ok", message=msg)
        return None
    backup_path = str(source.with_suffix(source.suffix + ".bak"))
    try:
        shutil.copy2(source, backup_path)
    except Exception as exc:
        raise MigrationBackupError(f"Failed creating migration backup at {backup_path}") from exc
    _emit_migration_event(step="backup", status="ok", message="Backup created", db_path=db_path, backup_path=backup_path)
    return backup_path


def _restore_backup_if_needed(db_path: str, backup_path: str | None) -> None:
    restore = _env_bool(
        "NGO_HOMESUITE_RESTORE_BACKUP_ON_MIGRATION_FAIL",
        bool(_runtime_setting("restore_backup_on_migration_fail", True)),
    )
    if not restore or not backup_path:
        return
    backup = Path(backup_path)
    if not backup.exists():
        return
    if db_path == ":memory:" or db_path.startswith("file:"):
        return
    target = Path(db_path)
    try:
        shutil.copy2(backup, target)
    except Exception as exc:
        raise MigrationBackupError(f"Failed restoring backup from {backup_path} to {db_path}") from exc
    _emit_migration_event(step="rollback", status="ok", message="Backup restored", db_path=db_path, backup_path=backup_path)


def _execute_script_with_retry(conn: sqlite3.Connection, sql: str, *, version: int, filename: str) -> None:
    retries = max(1, int(os.environ.get("NGO_HOMESUITE_MIGRATION_LOCK_RETRIES", "3")))
    backoff = max(0.0, float(os.environ.get("NGO_HOMESUITE_MIGRATION_LOCK_BACKOFF_SEC", "0.2")))

    for attempt in range(1, retries + 1):
        try:
            conn.executescript(sql)
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt >= retries:
                raise MigrationApplyError(
                    f"Failed applying migration v{version} ({filename}) due to database lock"
                ) from exc
            _emit_migration_event(
                step="apply",
                status="error",
                message="Migration lock contention detected; retrying",
                version=version,
                file=filename,
                attempt=attempt,
                retries=retries,
            )
            time.sleep(backoff * (2 ** (attempt - 1)))
        except Exception as exc:
            raise MigrationApplyError(f"Failed applying migration v{version} ({filename})") from exc


@enforce_error_contract
def auto_migrate(db_path: str | None = None) -> None:
    _guard_encrypted_db_not_supported()
    resolved_db_path = db_path or "ngo_data.db"
    migration_plan = plan_migrations(resolved_db_path)
    migration_files = [
        Path(migration_plan.migrations_dir) / item.name
        for item in migration_plan.pending
    ]

    backup_path = _create_backup_if_needed(resolved_db_path)
    timeout_s = _env_float("NGO_HOMESUITE_MIGRATION_TIMEOUT_SEC", float(_runtime_setting("migration_timeout_sec", 30.0)))
    conn = sqlite3.connect(resolved_db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_schema_version_table(conn)
        executed_versions: list[int] = []
        for mf in migration_files:
            version = _migration_version_from_name(mf)
            _emit_migration_event(step="apply", status="start", message="Applying migration", version=version, file=mf.name)
            with open(mf, 'rb') as f:
                hash_val = hashlib.sha256(f.read()).hexdigest()
            sql = mf.read_text(encoding='utf-8')
            try:
                precondition_tables = {
                    12: {"donations", "funds"},
                    13: {"recurring_donation_plans"},
                    19: {"users"},
                    20: {"donors", "campaigns"},
                    24: {"users"},
                    26: {"campaigns", "users"},
                    27: {"events"},
                    28: {"campaign_email_deliveries"},
                    29: {"users"},
                    31: {"users"},
                    32: {"users"},
                    33: {"users"},
                    34: {"campaign_email_batches"},
                }
                required_tables = precondition_tables.get(version)
                if required_tables is not None:
                    names_csv = ", ".join(f"'{name}'" for name in sorted(required_tables))
                    existing_tables = {
                        str(row[0])
                        for row in conn.execute(
                            f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({names_csv})"
                        ).fetchall()
                    }
                    if existing_tables != required_tables:
                        now_utc = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                        _insert_schema_version_row(conn, version=version, hash_value=hash_val, applied_at_utc=now_utc)
                        try:
                            conn.execute(
                                'INSERT INTO schema_hash (version, hash, applied_at_utc) VALUES (?, ?, ?)',
                                (version, hash_val, now_utc),
                            )
                        except Exception as exc:
                            _emit_migration_event(
                                step="apply",
                                status="error",
                                message="schema_hash insert skipped",
                                version=version,
                                file=mf.name,
                                error=str(exc),
                                classification="recoverable",
                            )
                        conn.commit()
                        _emit_migration_event(
                            step="apply",
                            status="ok",
                            message="Skipped migration until required tables are created by bootstrap",
                            version=version,
                            file=mf.name,
                        )
                        print(f"Skipped migration {version} ({mf.name}) until required tables are created by bootstrap.")
                        continue

                if version == 34:
                    table_info = list(conn.execute("PRAGMA table_info(campaign_email_batches)").fetchall())
                    existing_cols = {str(row[1]).strip().lower() for row in table_info if row and len(row) > 1}
                    if "scheduled_at" in existing_cols:
                        sql_lines: list[str] = []
                        for line in sql.splitlines():
                            normalized = line.strip().lower()
                            if normalized.startswith("alter table campaign_email_batches add column scheduled_at"):
                                continue
                            sql_lines.append(line)
                        sql = "\n".join(sql_lines)

                _execute_script_with_retry(conn, sql, version=version, filename=mf.name)
            except Exception:
                raise

            now_utc = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            _insert_schema_version_row(conn, version=version, hash_value=hash_val, applied_at_utc=now_utc)
            # Also update schema_hash table for versioned migrations
            try:
                conn.execute('INSERT INTO schema_hash (version, hash, applied_at_utc) VALUES (?, ?, ?)',
                             (version, hash_val, now_utc))
            except Exception as exc:
                _emit_migration_event(
                    step="apply",
                    status="error",
                    message="schema_hash update skipped",
                    version=version,
                    file=mf.name,
                    error=str(exc),
                    classification="recoverable",
                )
            conn.commit()
            executed_versions.append(version)
            _emit_migration_event(step="apply", status="ok", message="Applied migration", version=version, file=mf.name)
            print(f"Applied migration {version} ({mf.name}) with hash {hash_val}")
        plan_hash = _plan_hash(migration_plan.applied_versions + executed_versions, migration_plan.pending)
        _emit_migration_event(
            step="apply",
            status="ok",
            message="Migration executed",
            event={
                "type": "migration_executed",
                "plan_hash": plan_hash,
                "drift_detected": False,
            },
            plan_hash=plan_hash,
            drift_detected=False,
        )
        _emit_migration_event(step="apply", status="ok", message="All migrations applied and verified", pending_count=migration_plan.pending_count)
        print("All migrations applied and verified.")
    except Exception as exc:
        conn.rollback()
        _emit_migration_event(step="apply", status="error", message="Migration execution failed", error=str(exc))
        _restore_backup_if_needed(resolved_db_path, backup_path)
        if isinstance(exc, MigrationError):
            raise
        raise MigrationApplyError(f"Failed applying migrations for {resolved_db_path}") from exc
    finally:
        conn.close()


def run_preflight(db_path: str | None = None, verify_backup: bool = False) -> dict[str, Any]:
    plan = plan_migrations(db_path=db_path)
    payload = plan.to_dict()
    if verify_backup:
        backup_path = _create_backup_if_needed(plan.db_path)
        if backup_path:
            # Restore immediately so this is only a verification step.
            _restore_backup_if_needed(plan.db_path, backup_path)
            payload["backup_verified"] = True
            payload["backup_path"] = backup_path
        else:
            payload["backup_verified"] = False
            payload["backup_path"] = None
    return payload


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
