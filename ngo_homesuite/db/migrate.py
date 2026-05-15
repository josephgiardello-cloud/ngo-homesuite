import sqlite3
import hashlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path


class MigrationError(RuntimeError):
    """Raised when schema migration validation or execution fails."""


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
    target = Path(db_path)
    shutil.copy2(backup, target)

def auto_migrate(db_path=None):
    try:
        from ngo_homesuite.migrations import MIGRATIONS_DIR
    except ImportError:
        MIGRATIONS_DIR = Path(__file__).parent.parent / 'migrations'
    resolved_db_path = db_path or 'ngo_data.db'
    backup_path = _create_backup_if_needed(resolved_db_path)
    timeout_s = float(os.environ.get("NGO_HOMESUITE_MIGRATION_TIMEOUT_SEC", "30"))
    conn = sqlite3.connect(resolved_db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            cur = conn.execute('SELECT version, hash FROM schema_version ORDER BY version')
            applied = {row['version']: row['hash'] for row in cur.fetchall()}
        except sqlite3.OperationalError:
            applied = {}
        migration_files = sorted(MIGRATIONS_DIR.glob('*.sql'))
        for mf in migration_files:
            version = int(mf.name.split('_')[0])
            with open(mf, 'rb') as f:
                hash_val = hashlib.sha256(f.read()).hexdigest()
            if version in applied:
                if applied[version] != hash_val:
                    raise MigrationError(
                        f"Migration {mf.name} hash mismatch! DB: {applied[version]} File: {hash_val}"
                    )
                continue
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
