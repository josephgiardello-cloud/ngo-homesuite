# --- Imports and symbol order fix ---

from __future__ import annotations
import os
import atexit
import getpass
import hashlib
import hmac
import json
import logging
import queue
import re
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from typing import Any, Callable, Iterator, Mapping, TypeAlias, TypeVar, cast

# --- Security hooks (can be set by application for audit, policy, or custom checks) ---
SchemaMigrationHook: Callable[[sqlite3.Connection, str | None], None] | None = None
BackupHook: Callable[[Path], None] | None = None

# --- Internal config imports ---
from ..config import (
    BACKUP_DIRECTORY,
    DB_ENCRYPTION_KEY_ENV,
    DB_PATH,
    DB_POOL_SIZE,
    DB_SQLCIPHER_KDF_ITERATIONS,
    DB_SQLCIPHER_MIN_KDF_ITERATIONS,
    DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH,
    DB_SQLCIPHER_REQUIRE_HEX_KEY,
)

# --- Custom Exception for Fatal DB Errors ---
class FatalDBError(Exception):
    """Custom exception for fatal database errors."""
    pass

# --- Secure permissions helper ---
def _set_secure_permissions(path: Path, is_dir: bool = False) -> None:
    """Set secure permissions: 0600 for files, 0700 for directories (best effort, cross-platform)."""
    try:
        if os.name == "nt":
            # On Windows, skip chmod (use ACLs if needed)
            return
        if is_dir:
            path.chmod(0o700)
        else:
            path.chmod(0o600)
    except Exception:
        pass

def _check_and_handle_schema_migration(conn: sqlite3.Connection, db_path: str | None = None) -> None:
    """Centralized schema hash check and migration escape hatch logic."""
    _ensure_metadata_table(conn)
    allow_migration = os.environ.get("NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION", "0") in {"1", "true", "yes", "on"}
    if not check_metadata_hash(conn):
        if not allow_migration:
            if SchemaMigrationHook:
                SchemaMigrationHook(conn, db_path)
            raise FatalDBError(
                "Schema integrity check failed: stored HMAC does not match current schema. "
                "Database may have been tampered with or corrupted. "
                "Restore from a known-good backup or rekey with operator approval."
            )
        else:
            logger.warning(
                "Schema hash mismatch, but NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION escape hatch is enabled. Proceeding for migration.",
                extra={
                    "event_id": "db.schema.migration_escape",
                    "extra_fields": {"db_path": db_path, "escape_hatch": True}
                },
            )
            if SchemaMigrationHook:
                SchemaMigrationHook(conn, db_path)
    else:
        logger.info(
            "Database schema integrity verified (HMAC match)",
            extra={
                "event_id": "db.schema.integrity_ok",
                "extra_fields": {"db_path": db_path, "hmac_verified": True}
            },
        )





# --- Key provenance ledger helpers ---
def _ensure_provenance_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS __key_provenance__ (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            operator TEXT,
            old_key_fingerprint TEXT,
            new_key_fingerprint TEXT,
            signature TEXT
        )
    """)
    conn.commit()

def _key_fingerprint(key: str | None) -> str | None:
    if not key:
        return None
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def _append_external_audit_log(entry: Mapping[str, Any], log_type: str = "audit") -> None:
    """Append a signed, tamper-evident entry to an external audit log (append-only, file-locked if possible)."""
    import json
    log_env = {
        "provenance": "NGO_HOMESUITE_PROVENANCE_LOG",
        "schema": "NGO_HOMESUITE_SCHEMA_LOG",
        "backup": "NGO_HOMESUITE_BACKUP_LOG",
        "audit": "NGO_HOMESUITE_AUDIT_LOG",
    }
    log_path = os.environ.get(log_env.get(log_type, "audit"), f"ngo_homesuite_{log_type}.log")
    log_path_obj = Path(log_path)
    if not log_path_obj.exists():
        log_path_obj.parent.mkdir(parents=True, exist_ok=True)
        log_path_obj.touch(exist_ok=False)
        _set_secure_permissions(log_path_obj)
        try:
            import portalocker
            with open(log_path, "a", encoding="utf-8") as f:
                portalocker.lock(f, portalocker.LOCK_EX)
                f.write(json.dumps(entry, sort_keys=True, separators=(',', ':')) + "\n")
                portalocker.unlock(f)
        except ImportError:
            # Fallback to fcntl.flock on Unix
            import sys
            if sys.platform != "win32":
                import fcntl
                with open(log_path, "a", encoding="utf-8") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    f.write(json.dumps(entry, sort_keys=True, separators=(',', ':')) + "\n")
                    fcntl.flock(f, fcntl.LOCK_UN)
            else:
                raise FatalDBError("portalocker is required on Windows for safe audit logging. Please install portalocker.")
        except Exception as e:
            logger.error(
                f"Failed to write to external {log_type} log: {e}",
                extra={"event_id": f"{log_type}.log.write_failed", "extra_fields": {"error": str(e)}}
            )
            raise FatalDBError(f"Failed to write to external {log_type} log: {e}")


def log_key_provenance(conn: sqlite3.Connection, old_key: str, new_key: str, operator: str | None = None) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    if operator is None:
        operator = os.environ.get("NGO_HOMESUITE_OPERATOR") or getpass.getuser()
    old_fp = _key_fingerprint(old_key)
    new_fp = _key_fingerprint(new_key)
    entry: dict[str, str | None] = {
        "ts": ts,
        "event": "key_rotation",
        "operator": operator,
        "old_key_fingerprint": old_fp,
        "new_key_fingerprint": new_fp,
        "signature": os.environ.get("NGO_HOMESUITE_KEY_ROTATION_SIGNATURE"),
    }
    try:
        _ensure_provenance_table(conn)
        conn.execute(
            "INSERT INTO __key_provenance__ (ts, operator, old_key_fingerprint, new_key_fingerprint, signature) VALUES (?, ?, ?, ?, ?)",
            (ts, operator, old_fp, new_fp, entry["signature"])
        )
        conn.commit()
    except Exception as e:
        logger.error(
            f"Failed to write in-DB provenance: {e}",
            extra={"event_id": "provenance.db.write_failed", "extra_fields": {"error": str(e)}}
        )
        raise FatalDBError(f"Failed to write in-DB provenance: {e}")
    try:
        _append_external_audit_log(entry, log_type="provenance")
    except Exception as e:
        logger.error(
            f"Failed to write external provenance log: {e}",
            extra={"event_id": "provenance.log.write_failed", "extra_fields": {"error": str(e)}}
        )
        raise FatalDBError(f"Failed to write external provenance log: {e}")
    logger.info(
        "Key rotation event logged",
        extra={"event_id": "provenance.key_rotation", "extra_fields": entry}
    )
    # NOTE: The in-DB provenance is only trusted if the external log and signature are valid.


# --- Tamper-evident metadata helpers (HMAC-based) ---

def _ensure_metadata_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS __db_metadata__ (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()

def _get_hmac_key() -> bytes:
    # In production, consider integrating with a KMS or vault for key retrieval.
    key = os.environ.get("NGO_HOMESUITE_SCHEMA_HMAC_KEY")
    # TODO: Integrate with KMS or vault for key retrieval in production deployments.
    if not key:
        raise FatalDBError("NGO_HOMESUITE_SCHEMA_HMAC_KEY must be set for tamper-evident schema hashing.")
    return key.encode("utf-8")

def _schema_hmac(items: list[tuple[str, str | None]]) -> str:
    # items: list of (name, sql)
    h = hmac.new(_get_hmac_key(), digestmod=hashlib.sha256)
    for name, sql in items:
        h.update(name.encode())
        h.update((sql or '').encode())
    return h.hexdigest()

def update_metadata_hash(conn: sqlite3.Connection) -> None:
    # Require operator-supplied detached signature for schema hash update (check first, unconditionally)
    operator_signature = os.environ.get("NGO_HOMESUITE_SCHEMA_SIGNATURE")
    if not operator_signature:
        raise FatalDBError("NGO_HOMESUITE_SCHEMA_SIGNATURE must be set to a detached operator signature for schema hash update.")

    # All other logic must come after the signature check
    # Compute a HMAC of all user tables and schema
    cur = conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%'")
    items = sorted(cur.fetchall())
    digest = _schema_hmac(items)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "schema_update",
        "schema_hmac": digest,
        "operator_signature": operator_signature,
    }
    try:
        _append_external_audit_log(entry, log_type="schema")
    except Exception as e:
        logger.error(
            f"Failed to write external schema log: {e}",
            extra={"event_id": "schema.log.write_failed", "extra_fields": {"error": str(e)}}
        )
        raise FatalDBError(f"Failed to write external schema log: {e}")

    # Store hash in DB as before
    conn.execute("REPLACE INTO __db_metadata__ (key, value) VALUES (?, ?)", ("schema_hmac", digest))
    conn.commit()
    logger.info(
        "Schema hash updated and logged",
        extra={"event_id": "schema.hash_update", "extra_fields": entry}
    )

def check_metadata_hash(conn: sqlite3.Connection) -> bool:
    # Returns True if HMAC matches current schema and signature is present, False if tampered or unsigned
    cur = conn.execute("SELECT value FROM __db_metadata__ WHERE key='schema_hmac'")
    row = cur.fetchone()
    if not row:
        return False
    expected = row[0]
    cur2 = conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%'")
    items = sorted(cur2.fetchall())
    actual = _schema_hmac(items)
    # Enforce that signature is present in environment for verification
    operator_signature = os.environ.get("NGO_HOMESUITE_SCHEMA_SIGNATURE")
    if not operator_signature:
        logger.warning(
            "Schema HMAC present but no operator signature found in environment; cannot fully verify schema integrity.",
            extra={"event_id": "db.schema.signature_missing"}
        )
        return False
    return hmac.compare_digest(expected, actual)





# --- Dual-key window support (explicit, per-operation, thread-safe) ---

class DualKeyWindow:
    def __init__(self, old_key: str, new_key: str, expires_at: float) -> None:
        self.old_key = old_key
        self.new_key = new_key
        self.expires_at = expires_at
        self._lock = threading.Lock()

    def is_active(self) -> bool:
        with self._lock:
            return time.time() < self.expires_at

    def try_keys(self, conn: sqlite3.Connection) -> bool:
        """Try new_key, then old_key if window is active. Raise if both fail."""
        with self._lock:
            if not self.is_active():
                raise FatalDBError("Dual-key window is not active.")
            try:
                _sqlcipher_apply_key(conn, self.new_key)
                conn.execute("SELECT count(*) FROM sqlite_master")
                return True
            except Exception:
                try:
                    _sqlcipher_apply_key(conn, self.old_key)
                    conn.execute("SELECT count(*) FROM sqlite_master")
                    return True
                except Exception:
                    raise FatalDBError("Both new and old keys failed during dual-key window.")

# --- Global dual-key window (thread-safe) ---
_dual_key_window_lock = threading.Lock()
_dual_key_window: DualKeyWindow | None = None

def set_global_dual_key_window(window: DualKeyWindow | None) -> None:
    global _dual_key_window
    with _dual_key_window_lock:
        _dual_key_window = window

def get_global_dual_key_window() -> DualKeyWindow | None:
    with _dual_key_window_lock:
        return _dual_key_window

def make_dual_key_window(old_key: str, new_key: str, seconds: float) -> DualKeyWindow:
    return DualKeyWindow(old_key, new_key, time.time() + seconds)

# (moved to top)

# --- ATTACH DATABASE Hardening ---
# Set to True to block all ATTACH DATABASE statements, or set a whitelist of allowed paths.
ATTACH_DATABASE_BLOCK_ALL: bool = True  # Set to False to allow whitelisting
ATTACH_DATABASE_WHITELIST: set[str] = set()  # e.g., {"/allowed/path1.db", "/allowed/path2.db"}

# Thread-local context for trusted ATTACH (e.g., key rotation)
_attach_trusted_ctx = threading.local()

def _is_attach_trusted() -> bool:
    return getattr(_attach_trusted_ctx, "trusted", False)

def _install_attach_hardening(conn: sqlite3.Connection) -> None:
    # Use set_authorizer to intercept ATTACH
    def authorizer_cb(
        action: int,
        arg1: str | None,
        arg2: str | None,
        dbname: str | None,
        source: str | None,
    ) -> int:
        # SQLITE_ATTACH = 24 (see https://sqlite.org/c3ref/c_alter_table.html)
        SQLITE_ATTACH = 24
        if action == SQLITE_ATTACH:
            # Allow ATTACH if in trusted context (e.g., key rotation)
            if _is_attach_trusted():
                return sqlite3.SQLITE_OK
            if ATTACH_DATABASE_BLOCK_ALL:
                return sqlite3.SQLITE_DENY
            if ATTACH_DATABASE_WHITELIST and arg1 not in ATTACH_DATABASE_WHITELIST:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    try:
        conn.set_authorizer(authorizer_cb)
    except Exception:
        pass

from ..config import (
    BACKUP_DIRECTORY,
    DB_ENCRYPTION_KEY_ENV,
    DB_PATH,
    DB_POOL_SIZE,
    DB_SQLCIPHER_KDF_ITERATIONS,
    DB_SQLCIPHER_MIN_KDF_ITERATIONS,
    DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH,
    DB_SQLCIPHER_REQUIRE_HEX_KEY,
)



T = TypeVar("T")
DBConnection: TypeAlias = sqlite3.Connection


# --- Structured Logging Support ---
class _StructuredLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "event": getattr(record, "event_id", record.getMessage().split()[0].lower()),
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            base.update(cast(dict[str, Any], extra_fields))
        return json.dumps(base, separators=(",", ":"), sort_keys=True)

def _setup_structured_logging():
    # Hardened: Ignore NGO_HOMESUITE_JSON_LOGS if running as root/admin, and prevent handler hijack
    import sys
    is_admin = False
    try:
        if sys.platform == "win32":
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            is_admin = (os.geteuid() == 0)
    except Exception:
        pass

    if is_admin:
        return  # Never allow env-based log config as root/admin

    if os.environ.get("NGO_HOMESUITE_JSON_LOGS", "").lower() in {"1","true","yes","on"}:
        log = logging.getLogger("ngo_homesuite.structured")
        # Prevent duplicate handler hijack and only allow one structured handler
        for h in log.handlers:
            if isinstance(h, logging.StreamHandler) and isinstance(h.formatter, _StructuredLogFormatter):
                return
        handler = logging.StreamHandler()
        handler.setFormatter(_StructuredLogFormatter())
        log.addHandler(handler)
        log.propagate = True  # Allow messages to propagate to root if desired
        # Do not clear root handlers or force root level

_setup_structured_logging()
logger = logging.getLogger(__name__)


# Logging state lock for thread safety
_log_state_lock = threading.Lock()
_logged_db_driver: bool | None = None
_logged_key_modes: set[str] = set()
_logged_kdf_iter: int | None = None
_logged_pool: bool = False
_logged_sqlcipher_mem_security: bool = False

# TODO: Consider an async-compatible pool (e.g., using asyncio.Queue or external async pool libraries)
_pool_lock = threading.Lock()
_pool_max_size_value: int | None = None
_pool_created = 0
_pool: queue.LifoQueue[DBConnection] | None = None
_last_pool_block_log_ts: float = 0.0


def _pool_max_size() -> int:
    # Can be overridden at runtime for operators.
    size = _env_int("NGO_HOMESUITE_DB_POOL_SIZE", DB_POOL_SIZE)
    return max(1, size)


def _pool_init() -> None:
    global _pool, _pool_max_size_value, _logged_pool
    if _pool is not None:
        return
    _pool_max_size_value = _pool_max_size()
    _pool = queue.LifoQueue(maxsize=_pool_max_size_value)

    if not _logged_pool:
        logger.info(
            "DB connection pool enabled",
            extra={"event_id": "db.pool.enabled", "extra_fields": {"pool_size": _pool_max_size_value}},
        )
        _logged_pool = True

    atexit.register(_pool_close_all)


def _pool_close_all() -> None:
    global _pool
    pool = _pool
    if pool is None:
        return

    while True:
        try:
            conn = pool.get_nowait()
        except queue.Empty:
            break
        try:
            conn.close()
        except Exception:
            pass


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _is_db_corruption_error(exc: BaseException) -> bool:
    if not isinstance(exc, (sqlite3.DatabaseError, sqlite3.OperationalError)):
        return False
    msg = str(exc).lower()
    needles = (
        "database disk image is malformed",
        "database is malformed",
        "malformed",
        "file is not a database",
        "not a database",
        "database corruption",
        "disk i/o error",
    )
    return any(n in msg for n in needles)


def _backup_corrupt_db_copy() -> Path | None:
    """Best-effort copy of the DB file for forensics/recovery, with audit logging."""
    # TODO: Consider atomic copy or retry logic for more robust backup handling.
    src = Path(DB_PATH)
    if not src.exists():
        return None
    backup_dir = Path(BACKUP_DIRECTORY)
    backup_dir.mkdir(parents=True, exist_ok=True)
    _set_secure_permissions(backup_dir, is_dir=True)
    dest = backup_dir / f"{src.stem}_CORRUPT_{_utc_now_compact()}{src.suffix or '.db'}"
    try:
        shutil.copy2(src, dest)
        _set_secure_permissions(dest)
        if BackupHook:
            BackupHook(dest)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "backup_corrupt_db_copy",
            "src": str(src),
            "dest": str(dest),
            "operator": os.environ.get("NGO_HOMESUITE_OPERATOR") or getpass.getuser(),
        }
        try:
            _append_external_audit_log(entry, log_type="backup")
        except Exception as e:
            logger.error(
                f"Failed to write external backup log: {e}",
                extra={"event_id": "backup.log.write_failed", "extra_fields": {"error": str(e)}}
            )
            # Not fatal for backup, but should be monitored
        logger.info(
            "Corrupt DB backup created and logged",
            extra={"event_id": "backup.corrupt_db_copy", "extra_fields": entry}
        )
        return dest
    except OSError as e:
        logger.error(
            f"Failed to create corrupt DB backup: {e}",
            extra={"event_id": "backup.corrupt_db_copy_failed", "extra_fields": {"error": str(e)}}
        )
        return None


def _pool_acquire_connection() -> DBConnection:
    global _pool_created
    global _last_pool_block_log_ts
    with _pool_lock:
        _pool_init()
        pool = _pool
        max_size = _pool_max_size_value

    assert pool is not None
    assert max_size is not None

    def _validate_conn(conn: DBConnection) -> bool:
        try:
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _get_valid_conn_from_pool(
        pool: queue.LifoQueue[DBConnection],
        max_size: int,
    ) -> DBConnection | None:
        for _ in range(max_size):
            try:
                conn = pool.get_nowait()
            except queue.Empty:
                break
            if _validate_conn(conn):
                return conn
            else:
                try:
                    conn.close()
                except Exception:
                    pass
                with _pool_lock:
                    global _pool_created
                    _pool_created = max(0, _pool_created - 1)
        return None

    # Try to get a healthy connection from the pool (first pass)
    conn = _get_valid_conn_from_pool(pool, max_size)
    if conn:
        return conn

    with _pool_lock:
        # Re-check under lock in case another thread created/returned one.
        conn = _get_valid_conn_from_pool(pool, max_size)
        if conn:
            return conn
        if _pool_created < max_size:
            _pool_created += 1
            return connect_db()

    # Pool is exhausted; block until someone returns a connection.
    now = time.monotonic()
    if (now - _last_pool_block_log_ts) >= 5.0:
        logger.warning(
            "DB connection pool exhausted; waiting for a connection",
            extra={"event_id": "db.pool.exhausted", "extra_fields": {"pool_size": max_size}},
        )
        _last_pool_block_log_ts = now
    # Add timeout to prevent indefinite blocking
    timeout_sec = 30.0
    try:
        while True:
            try:
                conn = pool.get(timeout=timeout_sec)
            except queue.Empty:
                raise FatalDBError(f"DB connection pool exhausted: no connection available after {timeout_sec} seconds.")
            if _validate_conn(conn):
                return conn
            else:
                try:
                    conn.close()
                except Exception:
                    pass
                with _pool_lock:
                    _pool_created = max(0, _pool_created - 1)
    except Exception as e:
        raise FatalDBError(f"DB connection pool error: {e}")


def _pool_release_connection(conn: DBConnection) -> None:
    pool = _pool
    if pool is None:
        try:
            conn.close()
        except Exception:
            pass
        return

    with _pool_lock:
        try:
            pool.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return default
    return int(value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean for {name}: {raw!r}")


def _sqlcipher_policy() -> tuple[int, int, bool]:
    """Return (kdf_iter, min_passphrase_length, require_hex_key)."""

    # Environment overrides (optional)
    kdf_iter = _env_int("NGO_HOMESUITE_DB_KDF_ITER", DB_SQLCIPHER_KDF_ITERATIONS)
    min_len = _env_int(
        "NGO_HOMESUITE_DB_MIN_PASSPHRASE_LEN", DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH
    )
    require_hex = _env_bool("NGO_HOMESUITE_DB_REQUIRE_HEX_KEY", DB_SQLCIPHER_REQUIRE_HEX_KEY)

    if kdf_iter < DB_SQLCIPHER_MIN_KDF_ITERATIONS:
        raise ValueError(
            f"SQLCipher kdf_iter too low: {kdf_iter} (min {DB_SQLCIPHER_MIN_KDF_ITERATIONS})"
        )
    if min_len < 1:
        raise ValueError("Minimum passphrase length must be >= 1")

    return kdf_iter, min_len, require_hex


def _escape_sqlite_single_quotes(value: str) -> str:
    return value.replace("'", "''")


def _looks_like_hex(value: str) -> bool:
    v = value.strip()
    # Relaxed: allow shorter and odd-length hex for migration scenarios
    # WARNING: In production, monitor for weak/short hex keys. This is only safe for migration/controlled use.
    if len(v) < 8:
        return False
    return re.fullmatch(r"[0-9a-fA-F]+", v) is not None


def _sqlcipher_apply_key(conn: DBConnection, key: str) -> None:
    """Apply SQLCipher key.

    Supports:
    - passphrase in NGO_HOMESUITE_DB_KEY
    - hex key material when prefixed with 'hex:' (recommended)
      Example: NGO_HOMESUITE_DB_KEY=hex:001122... (even-length hex)
    """

    global _logged_kdf_iter

    key = key.strip()
    kdf_iter, min_len, require_hex = _sqlcipher_policy()

    if require_hex and not key.lower().startswith("hex:"):
        raise ValueError(
            "Hex-only SQLCipher key mode is enabled; set NGO_HOMESUITE_DB_KEY to 'hex:<hex-bytes>'"
        )

    if key.lower().startswith("hex:"):
        hex_key = key[4:].strip()
        if not _looks_like_hex(hex_key):
            logger.error("Invalid SQLCipher hex key format")
            raise ValueError("Invalid hex key format")

        with _log_state_lock:
            if "hex" not in _logged_key_modes:
                logger.info(
                    "Using SQLCipher with hex key",
                    extra={"event_id": "db.sqlcipher.hex_key"},
                )
                _logged_key_modes.add("hex")
        conn.execute(f"PRAGMA key = \"x'{hex_key.lower()}'\"")
        return

    with _log_state_lock:
        if "passphrase" not in _logged_key_modes:
            logger.info(
                "Using SQLCipher with passphrase key",
                extra={"event_id": "db.sqlcipher.passphrase_key"},
            )
            _logged_key_modes.add("passphrase")

    if len(key) < min_len:
        raise ValueError(
            f"SQLCipher passphrase too short: {len(key)} chars (min {min_len})"
        )

    # Strengthen passphrase protection.
    conn.execute("PRAGMA kdf_iter = ?", (kdf_iter,))
    with _log_state_lock:
        if _logged_kdf_iter != kdf_iter:
            logger.info("SQLCipher kdf_iter=%s", kdf_iter)
            _logged_kdf_iter = kdf_iter

    try:
        conn.execute("PRAGMA key = ?", (key,))
        return
    except (TypeError, sqlite3.Error):
        pass

    safe_key = _escape_sqlite_single_quotes(key)
    conn.execute(f"PRAGMA key = '{safe_key}'")


def _sqlcipher_apply_security_pragmas(conn: DBConnection) -> None:
    """Best-effort extra security and WAL encryption pragmas for SQLCipher.

    SQLCipher 4+ supports cipher_memory_security to reduce key material exposure.
    Enforces encrypted WAL mode (cipher_plaintext_header_size=0).
    """

    global _logged_sqlcipher_mem_security
    try:
        conn.execute("PRAGMA cipher_memory_security = ON")
        with _log_state_lock:
            if not _logged_sqlcipher_mem_security:
                logger.info("SQLCipher cipher_memory_security=ON")
                _logged_sqlcipher_mem_security = True
    except Exception:
        pass

    # Enforce encrypted WAL mode
    try:
        conn.execute("PRAGMA cipher_plaintext_header_size = 0")
        conn.execute("PRAGMA journal_mode = WAL")
        logger.info(
            "SQLCipher encrypted WAL mode enforced",
            extra={"event_id": "db.sqlcipher.wal_encrypted"},
        )
    except Exception:
        pass


def connect_db_at(path: str) -> DBConnection:
    """Connect to DB; uses SQLCipher if installed and NGO_HOMESUITE_DB_KEY is set.
    Logs resolved DB path, file owner, and permissions for auditability.
    Automatically tries both keys if dual-key window is active.
    """

    global _logged_db_driver
    resolved_path = os.path.abspath(path)
    file_info: dict[str, Any] = {}
    try:
        stat = os.stat(resolved_path)
        file_info["owner_uid"] = stat.st_uid if hasattr(stat, "st_uid") else None
        file_info["owner_gid"] = stat.st_gid if hasattr(stat, "st_gid") else None
        file_info["mode"] = oct(stat.st_mode & 0o777)
    except Exception as e:
        file_info["stat_error"] = str(e)

    logger.info(
        "Opening database file",
        extra={
            "event_id": "db.open.pathinfo",
            "extra_fields": {
                "resolved_path": resolved_path,
                **file_info,
            },
        },
    )


    key = os.environ.get(DB_ENCRYPTION_KEY_ENV)
    dual_window = get_global_dual_key_window()
    if key:
        try:
            from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore
        except ModuleNotFoundError:
            logger.error(
                "SQLCipher requested (%s is set) but pysqlcipher3 is not installed",
                DB_ENCRYPTION_KEY_ENV,
                extra={
                    "event_id": "db.sqlcipher.missing_driver",
                    "extra_fields": {"env": DB_ENCRYPTION_KEY_ENV}
                },
                exc_info=True
            )
            raise FatalDBError(
                f"{DB_ENCRYPTION_KEY_ENV} is set but SQLCipher driver isn't installed. "
                "Refusing to open an unencrypted database. Install SQLCipher support (e.g., pysqlcipher3) "
                "or unset the environment variable."
            )
        try:
            sqlcipher_any = cast(Any, sqlcipher)
            conn = cast(DBConnection, sqlcipher_any.connect(path))
            with _log_state_lock:
                if _logged_db_driver is not True:
                    logger.info("Using SQLCipher database driver", extra={"event_id": "db.sqlcipher.driver", "extra_fields": {"driver": "pysqlcipher3"}})
                    _logged_db_driver = True
            _sqlcipher_apply_security_pragmas(conn)
            _install_attach_hardening(conn)

            # --- Dual-key window integration (enforced globally) ---
            if dual_window and dual_window.is_active():
                dual_window.try_keys(conn)
            else:
                _sqlcipher_apply_key(conn, key)

            # --- Tamper-evident schema hash check ---
            try:
                allow_migration = os.environ.get("NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION", "0") in {"1", "true", "yes", "on"}
                if not check_metadata_hash(conn):
                    if not allow_migration:
                        raise FatalDBError(
                            "Schema integrity check failed: stored HMAC does not match current schema. "
                            "Database may have been tampered with or corrupted. "
                            "Restore from a known-good backup or rekey with operator approval."
                        )
                    else:
                        logger.warning(
                            "Schema hash mismatch, but NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION escape hatch is enabled. Proceeding for migration.",
                            extra={
                                "event_id": "db.schema.migration_escape",
                                "extra_fields": {"db_path": path, "escape_hatch": True}
                            },
                        )
                else:
                    logger.info(
                        "Database schema integrity verified (HMAC match)",
                        extra={
                            "event_id": "db.schema.integrity_ok",
                            "extra_fields": {"db_path": path, "hmac_verified": True}
                        },
                    )
            except Exception as e:
                logger.warning(
                    f"Schema hash check failed: {e}",
                    extra={
                        "event_id": "db.schema.hash_check_failed",
                        "extra_fields": {"db_path": path, "error": str(e)}
                    },
                    exc_info=True
                )
            return conn
        except (ValueError, sqlite3.Error, OSError) as e:
            logger.exception(
                "Failed to open encrypted database",
                extra={
                    "event_id": "db.sqlcipher.open_failed",
                    "extra_fields": {"db_path": path, "error": str(e)}
                },
                exc_info=True
            )
            raise FatalDBError(f"Failed to open encrypted DB: {e}")
        # No fallback: if SQLCipher is required but fails, always fatal.
    # ...existing code for sqlite3 fallback...

    # Ensure parent directory exists
    parent_dir = os.path.dirname(path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(path)
    _install_attach_hardening(conn)
    # --- Tamper-evident schema hash check for unencrypted DB ---
    try:
        _check_and_handle_schema_migration(conn, db_path=path)
    except Exception as e:
        logger.warning(
            f"Schema hash check failed: {e}",
            extra={
                "event_id": "db.schema.hash_check_failed",
                "extra_fields": {"db_path": path, "error": str(e)}
            },
            exc_info=True
        )
    return conn


def connect_db() -> DBConnection:
    return connect_db_at(DB_PATH)



def configure_connection(conn: DBConnection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
    except sqlite3.DatabaseError:
        pass
    # Install ATTACH DATABASE hardening
    _install_attach_hardening(conn)

@contextmanager
def open_db() -> Iterator[tuple[DBConnection, sqlite3.Cursor]]:
    """Open a DB connection and cursor (fresh per call)."""

    conn = _pool_acquire_connection()
    configure_connection(conn)
    cur = conn.cursor()
    try:
        yield conn, cur
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except sqlite3.Error:
            pass

        _pool_release_connection(conn)


def _is_transient_db_error(exc: BaseException) -> bool:
    if not isinstance(exc, (sqlite3.OperationalError, sqlite3.DatabaseError)):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def run_db(
    op: Callable[[Any, Any], T],
    *,
    write: bool = False,
    retries: int = 3,
    base_delay_s: float = 0.25,
) -> T:
    """Run a DB operation with safe scoping and transient-lock retries."""

    last_exc: BaseException | None = None
    for attempt in range(max(1, retries)):
        try:
            with open_db() as (conn, cur):
                result = op(conn, cur)
                if write:
                    conn.commit()
                return result
        except BaseException as exc:
            if _is_db_corruption_error(exc):
                # Don't keep bad connections around.
                _pool_close_all()

                backup_path = _backup_corrupt_db_copy()

                key_set = bool(os.environ.get(DB_ENCRYPTION_KEY_ENV))
                hint = (
                    f"If you use SQLCipher, verify {DB_ENCRYPTION_KEY_ENV} is correct. "
                    "Otherwise the database file may be corrupted."
                    if key_set
                    else "The database file may be corrupted."
                )
                backup_note = (
                    f"A best-effort copy was saved to: {backup_path}"
                    if backup_path is not None
                    else "A best-effort copy could not be created."
                )

                logger.exception("Database appears corrupted or unreadable")
                raise FatalDBError(
                    "Database appears corrupted or unreadable.\n"
                    f"Path: {DB_PATH}\n"
                    f"{hint}\n"
                    f"{backup_note}\n"
                    "Next steps: restore from a known-good backup (or create a new empty DB if appropriate)."
                )

            if _is_transient_db_error(exc) and attempt < retries - 1:
                last_exc = exc
                delay_s = base_delay_s * (2**attempt)
                logger.warning(
                    "DB locked/busy; retrying",
                    extra={
                        "event_id": "db.locked.retry",
                        "extra_fields": {
                            "delay_s": delay_s,
                            "attempt": attempt + 1,
                            "max_attempts": retries,
                        },
                    },
                )
                time.sleep(delay_s)
                continue
            if _is_transient_db_error(exc):
                last_exc = exc
                break
            raise

    raise FatalDBError(f"Database unavailable: {last_exc}")
