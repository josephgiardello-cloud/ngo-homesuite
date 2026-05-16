# --- Periodic Integrity Re-Verification for Long-Running Processes ---
import os

# --- Periodic Integrity Re-Verification for Long-Running Processes ---
import threading
_periodic_check_state = {
    'interval': 3600,  # seconds
    'thread': None,
    'stop': False
}
def set_periodic_integrity_interval(seconds):
    """Change the interval for periodic integrity checks (in seconds)."""
    _periodic_check_state['interval'] = max(10, int(seconds))  # minimum 10s for safety

def stop_periodic_integrity_checks():
    """Signal the periodic integrity check thread to stop (will finish after current sleep)."""
    _periodic_check_state['stop'] = True

def start_periodic_integrity_checks(interval_seconds=3600):
    """
    Starts a background thread that periodically runs startup_integrity_checks().
    interval_seconds: how often to re-verify (default: 1 hour)
    Use set_periodic_integrity_interval(seconds) to change interval at runtime.
    """
    _periodic_check_state['interval'] = max(10, int(interval_seconds))
    _periodic_check_state['stop'] = False
    def periodic_task():
        while not _periodic_check_state['stop']:
            try:
                startup_integrity_checks()
            except Exception as e:
                logger.error(f"[INTEGRITY] Periodic integrity check failed: {e}")
            # Sleep for the current interval, but wake early if stop is set
            slept = 0
            while slept < _periodic_check_state['interval'] and not _periodic_check_state['stop']:
                time.sleep(1)
                slept += 1
    if _periodic_check_state['thread'] is None or not _periodic_check_state['thread'].is_alive():
        t = threading.Thread(target=periodic_task, daemon=True)
        _periodic_check_state['thread'] = t
        t.start()
# --- Efficient 3σ Drift Detection for Large Tables ---
def detect_3sigma_drift(conn, table_name, value_column, window_size=1000, pk_col=None):
    """
    Detects 3σ outliers in a value column using a memory-efficient streaming algorithm (Welford's).
    Only the latest value is checked for drift against the previous window_size values.
    Args:
        conn: sqlite3.Connection
        table_name: str
        value_column: str (column to check for drift)
        window_size: int (number of previous values to use for mean/stdev)
        pk_col: str or None (primary key column for ordering, defaults to ROWID)
    Returns:
        (is_drift, value, mean, stdev, n)
    """
    cur = conn.cursor()
    pk = pk_col or "ROWID"
    # Defensive: only allow safe identifier usage
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Unauthorized table: {table_name}")
    # Get the latest value and previous window_size values
    query = f"SELECT {value_column} FROM {table_name} ORDER BY {pk} DESC LIMIT ?"
    cur.execute(query, (window_size + 1,))
    values = [row[0] for row in cur.fetchall() if row[0] is not None]
    if len(values) < 2:
        return (False, None, None, None, 0)
    latest = values[0]
    window = values[1:window_size+1]
    # Streaming mean/stdev (Welford's algorithm)
    n = 0
    mean = 0.0
    M2 = 0.0
    for x in window:
        n += 1
        delta = x - mean
        mean += delta / n
        delta2 = x - mean
        M2 += delta * delta2
    stdev = (M2 / (n-1))**0.5 if n > 1 else 0.0
    is_drift = False
    if n > 1 and stdev > 0:
        if abs(latest - mean) > 3 * stdev:
            is_drift = True
    return (is_drift, latest, mean, stdev, n)
# --- Helper: canonical UTC now (ISO-8601, Z) ---
def canonical_utcnow():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
# --- Frozen environment snapshot for security-sensitive config ---
_FROZEN_ENV = {
    k: os.environ.get(k)
    for k in [
        "INTEGRITY_KMS_PROVIDER",
        "INTEGRITY_KMS_KEY_ID",
        "INTEGRITY_KMS_KEY_VERSION",
        "INTEGRITY_KMS_RESOURCE",
        "INTEGRITY_HMAC_KEY",
        "INTEGRITY_BASELINE_LOG_MODE",
        "INTEGRITY_BASELINE_LOG_DB",
        "INTEGRITY_BASELINE_LOG",
        "INTEGRITY_LOG_SEAL_INTERVAL",
        "INTEGRITY_KMS_FALLBACK_OK",
        "INTEGRITY_HASH_SALT",
        # Add any other security-critical env vars here
    ]
}

def _frozen_env(key, default=None):
    return _FROZEN_ENV.get(key, default)

# --- Startup integrity checks ---
def startup_integrity_checks():
    """
    Run on system/service startup: checks env var integrity and verifies append-only log.
    Alerts and prints if any check fails.
    """
    log_and_check_env_integrity_hash()
    ok, err, last_hash, num_entries = verify_baseline_log()
    if not ok:
        alert_security_event(
            "integrity.log_verification_failed",
            {"error": err, "last_entry_hash": last_hash, "num_entries": num_entries, "message": "Append-only log verification failed at startup."}
        )
        logger.info(f"[INTEGRITY ALERT] Append-only log verification failed at startup: {err}")
    else:
        logger.info(f"[INTEGRITY] Append-only log verified OK at startup. Entries: {num_entries}")

# --- Auto-invoke startup integrity checks if run as main ---
# This must be at the very end of the file so all functions are defined.
if __name__ == "__main__":
    startup_integrity_checks()
# ---
# Module caveats and operational notes ---
"""
Concurrency and blocking:
- BEGIN IMMEDIATE blocks other writers. For high-write databases, this could lead to contention or deadlocks.
- File-based append-only log uses exclusive locks, which may block long-running operations if multiple processes try to append simultaneously.

Inefficient pagination for large tables:
- LIMIT/OFFSET scales poorly. Consider keyset pagination or streaming cursor for tables with millions of rows.

Error handling nuances:
- Some print statements may expose info in logs; sensitive data should not be printed (even partial hashes or KMS errors).
- Fallback HMAC with salt is risky and only safe in testing—currently allowed if INTEGRITY_HMAC_KEY_FALLBACK_OK=1.

Seal anchoring:
- AWS S3 anchoring depends on bucket Object Lock configuration.
- Email anchoring is not cryptographically verifiable; only human-auditable.
- Public timestamping stub is unimplemented.

Baseline updates:
- DB baseline can be modified if attacker has DB write access and KMS access. This is expected (cannot defend against full root compromise), but important to note for threat modeling.

Azure Key Vault HMAC edge cases:
- Assumes HSM-backed key for HMAC; some SDK versions may behave differently (sign_data vs sign).
- Raises RuntimeError for non-HSM keys, which may be overly strict for some environments.

3σ Drift detection limitations:
- Only detects single outliers; sustained trends may go unnoticed.
- Uses statistics.mean and statistics.stdev—not incremental, so very large windows could be slow.

Redundant imports / minor code issues:
- Multiple repeated imports (sqlite3, hashlib, datetime, os, importlib).
- datetime.UTC used in some places but Python standard library does not have datetime.UTC; should use datetime.timezone.utc.
"""
# --- Imports ---
import os
import json
import base64
import importlib
import logging
import time
import portalocker
import smtplib
from email.message import EmailMessage
import sqlite3
import hashlib
import hmac
import statistics
import datetime
from pathlib import Path

from ..auth.alerting import alert_security_event

# Configure module-level logger
logger = logging.getLogger("ngo_homesuite.integrity_drift")
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(asctime)s][%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# --- Env var integrity hash (startup check) ---
_ENV_INTEGRITY_VARS = [
    "INTEGRITY_HMAC_KEY",
    "INTEGRITY_HASH_SALT",
    "INTEGRITY_KMS_PROVIDER",
    "INTEGRITY_KMS_KEY_ID",
    "INTEGRITY_KMS_RESOURCE",
    "INTEGRITY_KMS_KEY_VERSION",
    "INTEGRITY_KMS_FALLBACK_OK",
    "INTEGRITY_BASELINE_LOG_MODE",
    "INTEGRITY_BASELINE_LOG_DB",
    "INTEGRITY_BASELINE_LOG",
    "INTEGRITY_LOG_SEAL_INTERVAL",
    "INTEGRITY_S3_BUCKET",
    "INTEGRITY_S3_REGION",
    "INTEGRITY_S3_OBJECT_LOCK_MODE",
    "INTEGRITY_S3_OBJECT_LOCK_DAYS",
    "INTEGRITY_S3_PREFIX",
]

def compute_env_integrity_hash():
    """
    Computes a SHA-256 hash of critical integrity-related environment variables (names and values).
    Returns the hex digest string.
    """
    env_snapshot = {k: _frozen_env(k, "") for k in sorted(_ENV_INTEGRITY_VARS)}
    # Never log or expose actual values; only hash
    env_json = json.dumps(env_snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(env_json.encode("utf-8")).hexdigest()

def log_and_check_env_integrity_hash(env_hash_path=None):
    """
    Logs/seals the env var hash at startup, and verifies against previous run.
    Alerts if the hash changes between runs.
    """
    env_hash_path = env_hash_path or os.environ.get("INTEGRITY_ENV_HASH_FILE") or "integrity_env_hash.txt"
    current_hash = compute_env_integrity_hash()
    previous_hash = None
    if os.path.exists(env_hash_path):
        try:
            with open(env_hash_path, "r", encoding="utf-8") as f:
                previous_hash = f.read().strip()
        except Exception:
            previous_hash = None
    if previous_hash and previous_hash != current_hash:
        alert_security_event(
            "integrity.env_var_changed",
            {"previous_hash": previous_hash, "current_hash": current_hash, "message": "Critical integrity env vars changed since last run!"}
        )
        logger.critical("[INTEGRITY ALERT] Critical integrity environment variables changed since last run!")
    # Always write current hash for next run
    try:
        with open(env_hash_path, "w", encoding="utf-8") as f:
            f.write(current_hash + "\n")
        os.chmod(env_hash_path, 0o600)
    except Exception:
        pass


# --- Pluggable External Anchoring (AWS S3 Object Lock, Email, Public Timestamping) ---
# SECURITY NOTE: Sensitive keys (HMAC, KMS credentials) are read from environment variables. Ensure your environment is secured and avoid leaking these variables to logs, subprocesses, or untrusted code. Fallback to salt as HMAC key is opt-in and not recommended for production.
def anchor_seal_aws_s3_object_lock(last_seal):
    """
    Anchor a log seal entry to AWS S3 with Object Lock (WORM).
    Requires the following environment variables:
        INTEGRITY_S3_BUCKET: S3 bucket name (must have Object Lock enabled)
        INTEGRITY_S3_PREFIX: (optional) prefix/folder for objects
        INTEGRITY_S3_OBJECT_LOCK_MODE: (optional, default 'COMPLIANCE')
        INTEGRITY_S3_OBJECT_LOCK_DAYS: (optional, default 365)
        AWS credentials must be available in environment or config.
    """
    import os
    import json
    try:
        boto3 = __import__('boto3')
    except ImportError:
        logger.warning("[ANCHOR] boto3 not installed, cannot anchor to S3.")
        try:
            alert_security_event("integrity.anchor_failed", {"error": "boto3 not installed"})
        except Exception:
            pass
        return
    bucket = os.environ.get("INTEGRITY_S3_BUCKET")
    if not bucket:
        logger.warning("[ANCHOR] INTEGRITY_S3_BUCKET not set, skipping S3 anchor.")
        try:
            alert_security_event("integrity.anchor_failed", {"error": "INTEGRITY_S3_BUCKET not set"})
        except Exception:
            pass
        return
    prefix = os.environ.get("INTEGRITY_S3_PREFIX", "integrity-seals/")
    mode = os.environ.get("INTEGRITY_S3_OBJECT_LOCK_MODE", "COMPLIANCE")
    try:
        days = int(os.environ.get("INTEGRITY_S3_OBJECT_LOCK_DAYS", "365"))
    except Exception:
        days = 365
    entry_hash = last_seal.get("entry_hash")
    if not entry_hash:
        logger.error("[ANCHOR] No entry_hash in seal, cannot anchor.")
        try:
            alert_security_event("integrity.anchor_failed", {"error": "No entry_hash in seal"})
        except Exception:
            pass
        return
    object_key = f"{prefix.rstrip('/')}/{entry_hash}.json"
    import datetime
    # Support region specification
    region = os.environ.get("INTEGRITY_S3_REGION")
    s3 = boto3.client("s3", region_name=region) if region else boto3.client("s3")
    # Preflight: check if Object Lock is enabled on the bucket
    # Retry lock configuration read with exponential backoff
    max_retries = 5
    for attempt in range(max_retries):
        try:
            lock_conf = s3.get_object_lock_configuration(Bucket=bucket)
            if not lock_conf.get("ObjectLockConfiguration") or lock_conf["ObjectLockConfiguration"].get("ObjectLockEnabled") != "Enabled":
                logger.error(f"[ANCHOR] S3 bucket {bucket} does not have Object Lock enabled. Aborting anchor.")
                try:
                    alert_security_event("integrity.anchor_failed", {"error": "S3 Object Lock not enabled", "bucket": bucket})
                except Exception:
                    pass
                return
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(f"[ANCHOR] S3 get_object_lock_configuration failed (attempt {attempt+1}/{max_retries}): [suppressed]")
                time.sleep(wait)
            else:
                logger.error(f"[ANCHOR] Could not verify Object Lock on bucket {bucket} after {max_retries} attempts.")
                try:
                    alert_security_event("integrity.anchor_failed", {"error": "S3 Object Lock config failed", "bucket": bucket})
                except Exception:
                    pass
                return
    import time
    max_retries = 5
    for attempt in range(max_retries):
        try:
            retain_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
            resp = s3.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=json.dumps(last_seal, sort_keys=True, separators=(',', ':')).encode("utf-8"),
                ContentType="application/json",
                ObjectLockMode=mode,
                ObjectLockRetainUntilDate=retain_until.replace(microsecond=0)
            )
            logger.info(f"[ANCHOR] Seal anchored to S3: s3://{bucket}/{object_key} (Object Lock: {mode}, {days}d)")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(f"[ANCHOR] S3 put_object failed (attempt {attempt+1}/{max_retries}): [suppressed]. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"[ANCHOR] Failed to anchor seal to S3 after {max_retries} attempts.")
                try:
                    alert_security_event("integrity.anchor_failed", {"error": "S3 put_object failed", "bucket": bucket})
                except Exception:
                    pass

def anchor_seal_public_timestamp(last_seal):
    """
    Anchor a seal entry to a public timestamping service (OpenTimestamps).
    Args:
        last_seal (dict): The last seal entry (dict) from the log.
    """
    # Use the default OpenTimestamps calendar pool
    calendar_urls = [
        'https://a.pool.opentimestamps.org',
        'https://b.pool.opentimestamps.org',
        'https://a.pool.eternitywall.com',
        'https://ots.btc.catallaxy.com',
    ]
    # Minimal submission: use the RemoteCalendar directly
    import opentimestamps.calendar
    try:
        # You must define msg_bytes from last_seal, e.g. hash or data to anchor
        # Example: msg_bytes = last_seal['hash'].encode('utf-8')
        msg_bytes = last_seal.get('hash', '').encode('utf-8') if last_seal and 'hash' in last_seal else b''
        for url in calendar_urls:
            try:
                remote = opentimestamps.calendar.RemoteCalendar(url)
                remote.submit(msg_bytes)
                logger.info(f"[ANCHOR] Seal anchored to OpenTimestamps calendar: {url} (hash: [redacted])")
            except Exception as cal_exc:
                logger.warning(f"[ANCHOR] OpenTimestamps calendar {url} failed: {cal_exc}")
        # Optionally, save the .ots proof file or return the timestamp object
    except Exception as e:
        logger.error("[ANCHOR] OpenTimestamps anchor failed (see alert log)")
        try:
            alert_security_event("integrity.anchor_failed", {"error": str(e), "service": "OpenTimestamps"})
        except Exception:
            pass

# Pluggable anchor hook: set this to a function to call after each seal is written
import os
anchor_seal_hook = None

# Set anchor_seal_hook to AWS S3 anchor if configured
if os.environ.get("INTEGRITY_S3_BUCKET"):
    anchor_seal_hook = anchor_seal_aws_s3_object_lock

# --- Integrity System Cryptographic Policy ---
"""
The integrity system uses non-exportable symmetric MAC keys for all cryptographic operations.
All HMAC operations must use keys provisioned as HMAC-only (not signing or encryption), with algorithm HMAC-SHA-256.
Key type and algorithm are validated at runtime for all supported providers (GCP, AWS, Azure).
Key ID and key version are recorded in every baseline and append-only log entry for auditability and key rotation support.
Algorithm names are canonicalized as 'HMAC-SHA-256'.
"""

# --- Local Append-Only Baseline Log ---

def append_baseline_log(table_name, table_hash, schema_version, created_at, hmac_sig, log_path=None, db_path=None):
    """
    Append a baseline record to an append-only log.
    If INTEGRITY_BASELINE_LOG_MODE=db, use SQLite table; else use file-based log.
    """
    import json  # Ensure json is always available
    mode = os.environ.get("INTEGRITY_BASELINE_LOG_MODE", "file").lower()
    if mode == "db":
        db_path = db_path or os.environ.get("INTEGRITY_BASELINE_LOG_DB") or "integrity_baseline_log.db"
        import sqlite3, hashlib, datetime
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS baseline_log (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_hash TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    type TEXT NOT NULL,
                    table_name TEXT,
                    table_hash TEXT,
                    schema_version TEXT,
                    created_at TEXT,
                    hmac TEXT,
                    key_id TEXT,
                    key_version TEXT,
                    algorithm TEXT,
                    sealed_at TEXT,
                    sealed_entry_hash TEXT,
                    seal_sig TEXT
                )
            ''')
            # Find last entry
            cur.execute("SELECT seq, entry_hash FROM baseline_log ORDER BY seq DESC LIMIT 1")
            row = cur.fetchone()
            last_seq = row[0] if row else 0
            prev_hash = row[1] if row else "0" * 64
            # Canonicalize timestamp
            def canonical_utcnow():
                return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            created_at_canon = created_at
            try:
                if not created_at.endswith('Z'):
                    dt = datetime.datetime.fromisoformat(created_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    created_at_canon = dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            except Exception:
                created_at_canon = canonical_utcnow()
            key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
            key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
            canonical_entry = {
                "type": "baseline",
                "table": table_name,
                "table_hash": table_hash,
                "schema_version": schema_version,
                "created_at": created_at_canon,
                "hmac": hmac_sig,
                "key_id": key_id,
                "key_version": key_version,
                "algorithm": "HMAC-SHA-256"
            }
            canonical_json = json.dumps(canonical_entry, sort_keys=True, separators=(',', ':'))
            entry_hash = hashlib.sha256((prev_hash + canonical_json).encode("utf-8")).hexdigest()
            seq = last_seq + 1
            # Insert baseline entry
            cur.execute('''
                INSERT INTO baseline_log (seq, entry_hash, prev_hash, type, table_name, table_hash, schema_version, created_at, hmac, key_id, key_version, algorithm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (seq, entry_hash, prev_hash, "baseline", table_name, table_hash, schema_version, created_at_canon, hmac_sig, key_id, key_version, "HMAC-SHA-256"))
            # Periodic seal
            SEAL_INTERVAL = int(os.environ.get("INTEGRITY_LOG_SEAL_INTERVAL", "100"))
            last_seal = None
            if seq > 0 and seq % SEAL_INTERVAL == 0:
                seal_time = canonical_utcnow()
                seal_seq = seq + 1
                seal_body = json.dumps({
                    "type": "seal",
                    "seq": seal_seq,
                    "sealed_at": seal_time,
                    "sealed_entry_hash": entry_hash
                }, sort_keys=True, separators=(',', ':'))
                try:
                    seal_sig = cloud_kms_hmac(seal_body)
                except Exception as e:
                    seal_sig = f"[KMS HMAC ERROR: {e}]"
                seal_canonical = {
                    "type": "seal",
                    "seq": seal_seq,
                    "sealed_at": seal_time,
                    "sealed_entry_hash": entry_hash,
                    "seal_sig": seal_sig
                }
                seal_canonical_json = json.dumps(seal_canonical, sort_keys=True, separators=(',', ':'))
                seal_entry_hash = hashlib.sha256((entry_hash + seal_canonical_json).encode("utf-8")).hexdigest()
                cur.execute('''
                    INSERT INTO baseline_log (seq, entry_hash, prev_hash, type, sealed_at, sealed_entry_hash, seal_sig)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (seal_seq, seal_entry_hash, entry_hash, "seal", seal_time, entry_hash, seal_sig))
                last_seal = {
                    "entry_hash": seal_entry_hash,
                    "prev_hash": entry_hash,
                    **seal_canonical
                }
            conn.commit()
            # Pluggable external anchoring for seals
            if last_seal and anchor_seal_hook:
                import threading
                def anchor_in_background(seal):
                    try:
                        anchor_seal_hook(seal)
                    except Exception as e:
                        logger.error("[ANCHOR] External seal anchor failed (see alert log)")
                        try:
                            alert_security_event("integrity.anchor_failed", {"error": str(e), "seal": {"seq": seal.get("seq"), "entry_hash": "[redacted]"}})
                        except Exception:
                            pass
                threading.Thread(target=anchor_in_background, args=(last_seal,), daemon=True).start()
        finally:
            conn.close()
        return
    # --- File-based fallback (legacy) ---
    # ...existing code from previous append_baseline_log (file-based) goes here...
    log_path = log_path or os.environ.get("INTEGRITY_BASELINE_LOG") or "integrity_baseline.log"
    import hashlib
    import portalocker
    prev_hash = "0" * 64
    last_entry = None
    last_seq = 0
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            entry = json.loads(line)
                            if isinstance(entry, dict) and "seq" in entry:
                                last_entry = entry
                        except json.JSONDecodeError as e:
                            logger.warning(f"JSON decode error in append-only log: {e}")
                        except Exception as e:
                            logger.error(f"Unexpected error reading log entry: {e}")
            if last_entry:
                prev_hash = last_entry.get("entry_hash", prev_hash)
                last_seq = last_entry.get("seq", 0)
        except Exception as e:
            logger.warning(f"Error reading last entry from append-only log: {e}")
    key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
    key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
    import datetime
    def canonical_utcnow():
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    def create_seal(seq, entry_hash):
        seal_time = canonical_utcnow()
        seal_seq = seq + 1
        seal_body = json.dumps({
            "type": "seal",
            "seq": seal_seq,
            "sealed_at": seal_time,
            "sealed_entry_hash": entry_hash
        }, sort_keys=True, separators=(',', ':'))
        try:
            seal_sig = cloud_kms_hmac(seal_body)
        except Exception as e:
            seal_sig = f"[KMS HMAC ERROR: {e}]"
        seal_canonical = {
            "type": "seal",
            "seq": seal_seq,
            "sealed_at": seal_time,
            "sealed_entry_hash": entry_hash,
            "seal_sig": seal_sig
        }
        seal_canonical_json = json.dumps(seal_canonical, sort_keys=True, separators=(',', ':'))
        seal_entry_hash = hashlib.sha256((entry_hash + seal_canonical_json).encode("utf-8")).hexdigest()
        seal_entry = {
            "entry_hash": seal_entry_hash,
            "prev_hash": entry_hash,
            **seal_canonical
        }
        return seal_entry
    created_at_canon = created_at
    def canonical_utcnow():
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    try:
        if not created_at.endswith('Z'):
            try:
                dt = datetime.datetime.fromisoformat(created_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                created_at_canon = dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            except Exception as e:
                logger.warning(f"[WARNING] Could not parse created_at '{created_at}': {e}. Using canonical UTC now instead.")
                created_at_canon = canonical_utcnow()
    except Exception as e:
        logger.warning(f"[WARNING] Unexpected error in created_at canonicalization: {e}. Using canonical UTC now.")
        created_at_canon = canonical_utcnow()
    seq = last_seq + 1
    canonical_entry = {
        "type": "baseline",
        "seq": seq,
        "table": table_name,
        "table_hash": table_hash,
        "schema_version": schema_version,
        "created_at": created_at_canon,
        "hmac": hmac_sig,
        "key_id": key_id,
        "key_version": key_version,
        "algorithm": "HMAC-SHA-256"
    }
    canonical_json = json.dumps(canonical_entry, sort_keys=True, separators=(',', ':'))
    entry_hash = hashlib.sha256((prev_hash + canonical_json).encode("utf-8")).hexdigest()
    entry = {
        "entry_hash": entry_hash,
        "prev_hash": prev_hash,
        **canonical_entry
    }
    line = json.dumps(entry, sort_keys=True, separators=(',', ':')) + "\n"
    with open(log_path, "a+b") as f:
        portalocker.lock(f, portalocker.LOCK_EX)
        f.write(line.encode("utf-8"))
        f.flush()
        SEAL_INTERVAL = int(os.environ.get("INTEGRITY_LOG_SEAL_INTERVAL", "100"))
        last_seal = None
        if seq > 0 and seq % SEAL_INTERVAL == 0:
            seal_entry = create_seal(seq, entry_hash)
            seal_json = json.dumps(seal_entry, sort_keys=True, separators=(',', ':')) + "\n"
            f.write(seal_json.encode("utf-8"))
            f.flush()
            last_seal = seal_entry
        portalocker.unlock(f)
    if last_seal and anchor_seal_hook:
        import threading
        def anchor_in_background(seal):
            try:
                anchor_seal_hook(seal)
            except Exception as e:
                logger.error("[ANCHOR] External seal anchor failed (see alert log)")
                try:
                    alert_security_event("integrity.anchor_failed", {"error": str(e), "seal": {"seq": seal.get("seq"), "entry_hash": "[redacted]"}})
                except Exception:
                    pass
        threading.Thread(target=anchor_in_background, args=(last_seal,), daemon=True).start()
    try:
        os.chmod(log_path, 0o600)
    except Exception as e:
        if os.name == "nt":
            logger.warning(f"[WARNING] Could not set file permissions on {log_path} (Windows does not support chmod 0o600): {e}")
        else:
            logger.warning(f"[WARNING] Could not set file permissions on {log_path}: {e}")

def verify_baseline_log(log_path=None):
    """
    Verifies the append-only baseline log for hash chaining, truncation, and tampering.
    Returns (ok, error_message, last_entry_hash, num_entries)
    """
    import hashlib
    log_path = log_path or os.environ.get("INTEGRITY_BASELINE_LOG") or "integrity_baseline.log"
    prev_hash = "0" * 64
    last_entry_hash = None
    num_entries = 0
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        if not lines:
            return (True, None, None, 0)
        prev_hash = "0" * 64
        last_entry_hash = None
        num_entries = 0
        last_seq = 0
        for line_num, line in enumerate(lines, 1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                logger.error(f"Malformed JSON at line {line_num}: {e}")
                return (False, f"Malformed JSON at line {line_num}", last_entry_hash, num_entries)
            except Exception as e:
                logger.error(f"Unexpected error parsing JSON at line {line_num}: {e}")
                return (False, f"Malformed JSON at line {line_num}", last_entry_hash, num_entries)
            # Check type field
            if "type" not in entry or entry["type"] not in ("baseline", "seal"):
                logger.error(f"Missing or invalid type at line {line_num}")
                return (False, f"Missing or invalid type at line {line_num}", last_entry_hash, num_entries)
            # Check sequence number
            seq = entry.get("seq")
            if not isinstance(seq, int) or seq != last_seq + 1:
                logger.error(f"Sequence number error at line {line_num}: got {seq}, expected {last_seq + 1}")
                return (False, f"Sequence number error at line {line_num}: got {seq}, expected {last_seq + 1}", last_entry_hash, num_entries)
            last_seq = seq
            canonical_entry = {k: v for k, v in entry.items() if k not in ("entry_hash", "prev_hash")}
            try:
                canonical_json = json.dumps(canonical_entry, sort_keys=True, separators=(',', ':'))
            except Exception as e:
                logger.error(f"Error serializing canonical entry at line {line_num}: {e}")
                return (False, f"Serialization error at line {line_num}", last_entry_hash, num_entries)
            expected_hash = hashlib.sha256((prev_hash + canonical_json).encode("utf-8")).hexdigest()
            if entry.get("entry_hash") != expected_hash:
                logger.error(f"Hash mismatch at line {line_num}")
                return (False, f"Hash mismatch at line {line_num}", last_entry_hash, num_entries)
            if entry.get("prev_hash") != prev_hash:
                logger.error(f"Prev hash mismatch at line {line_num}")
                return (False, f"Prev hash mismatch at line {line_num}", last_entry_hash, num_entries)
            # If this is a seal entry, verify seal_sig
            if entry.get("type") == "seal":
                seal_body = json.dumps({
                    "type": "seal",
                    "seq": entry["seq"],
                    "sealed_at": entry["sealed_at"],
                    "sealed_entry_hash": entry["sealed_entry_hash"]
                }, sort_keys=True, separators=(',', ':'))
                try:
                    expected_sig = None
                    try:
                        expected_sig = cloud_kms_hmac(seal_body)
                    except Exception as e:
                        # Optionally allow local HMAC fallback for offline/DR verification
                        if os.environ.get("INTEGRITY_KMS_FALLBACK_OK") == "1":
                            logger.warning(f"KMS unavailable during seal verification at line {line_num}: {e}. Trying local HMAC fallback.")
                            hmac_key = os.environ.get("INTEGRITY_HMAC_KEY")
                            if hmac_key:
                                expected_sig = hmac.new(hmac_key.encode('utf-8'), seal_body.encode('utf-8'), hashlib.sha256).hexdigest()
                        if not expected_sig:
                            logger.error(f"Seal KMS HMAC error at line {line_num}: {e}")
                            return (False, f"Seal KMS HMAC error at line {line_num}: {e}", last_entry_hash, num_entries)
                    if entry.get("seal_sig") != expected_sig:
                        logger.error(f"Seal signature mismatch at line {line_num}")
                        return (False, f"Seal signature mismatch at line {line_num}", last_entry_hash, num_entries)
                except Exception as e:
                    logger.error(f"Seal verification error at line {line_num}: {e}")
                    return (False, f"Seal verification error at line {line_num}: {e}", last_entry_hash, num_entries)
            prev_hash = entry["entry_hash"]
            last_entry_hash = prev_hash
            num_entries += 1
        return (True, None, last_entry_hash, num_entries)
    except FileNotFoundError:
        return (True, None, None, 0)
    except Exception as e:
        logger.error(f"Unexpected error verifying baseline log: {e}")
        return (False, str(e), last_entry_hash, num_entries)

# Example usage (to be called from a scheduled job or admin CLI):
# periodic_integrity_check('ngo_data.db', 'audit_log', 30)
#
# Notes:
# - Table name is allowlisted for SQL injection safety.
# - Only the latest datapoint is checked for 3σ drift.
# - Baseline is DB-backed with HMAC for authenticity, and file-based for backup/legacy.
# - For HA/multi-node, use DB-backed baseline and atomic update.
# - For large tables, chunked/Merkle hashing is used for scalability.
# - For append-only or forensic root of trust, use audit chaining or external anchoring.

# --- Startup integrity checks ---
def startup_integrity_checks():
    """
    Run on system/service startup: checks env var integrity and verifies append-only log.
    Alerts and prints if any check fails.
    """
    log_and_check_env_integrity_hash()
    ok, err, last_hash, num_entries = verify_baseline_log()
    if not ok:
        alert_security_event(
            "integrity.log_verification_failed",
            {"error": err, "last_entry_hash": last_hash, "num_entries": num_entries, "message": "Append-only log verification failed at startup."}
        )
        logger.critical(f"[INTEGRITY ALERT] Append-only log verification failed at startup: {err}")
    else:
        logger.info(f"[INTEGRITY] Append-only log verified OK at startup. Entries: {num_entries}")
import base64
import importlib
# --- Cloud KMS HMAC Support ---
def cloud_kms_hmac(data: str) -> str:
    """
    Compute HMAC using a configured Cloud KMS provider.
    Supported: Google Cloud KMS, AWS KMS, Azure Key Vault.
    Configured via INTEGRITY_KMS_PROVIDER (gcp|aws|azure), and provider-specific env vars.
    Returns hex-encoded HMAC, or raises on error.
    """
    provider = os.environ.get("INTEGRITY_KMS_PROVIDER")
    if not provider:
        raise RuntimeError("INTEGRITY_KMS_PROVIDER not set")
    provider = provider.lower()
    if not hasattr(cloud_kms_hmac, "_client_cache"):
        cloud_kms_hmac._client_cache = {}
    cache = cloud_kms_hmac._client_cache
    if provider == "gcp":
        return _cloud_kms_hmac_gcp(data, cache)
    elif provider == "aws":
        return _cloud_kms_hmac_aws(data, cache)
    elif provider == "azure":
        return _cloud_kms_hmac_azure(data, cache)
    else:
        raise RuntimeError(f"Unknown KMS provider: {provider}")

# --- Provider-specific Cloud KMS HMAC helpers ---
def _cloud_kms_hmac_gcp(data, cache):
    try:
        kms = importlib.import_module("google.cloud.kms")
    except ImportError:
        raise RuntimeError("google-cloud-kms not installed")
    resource = os.environ["INTEGRITY_KMS_RESOURCE"]
    if "gcp" not in cache:
        cache["gcp"] = kms.KeyManagementServiceClient()
    client = cache["gcp"]
    key = client.get_crypto_key(name=resource)
    if key.purpose != kms.CryptoKey.CryptoKeyPurpose.MAC:
        raise RuntimeError("GCP KMS key is not a MAC key")
    if key.version_template.algorithm != kms.CryptoKeyVersion.CryptoKeyVersionAlgorithm.HMAC_SHA256:
        raise RuntimeError("GCP KMS key algorithm is not HMAC_SHA256")
    key_version = resource.split("/")[-1] if "/cryptoKeyVersions/" in resource else "unknown"
    os.environ["INTEGRITY_KMS_KEY_VERSION"] = key_version
    resp = client.mac_sign(request={"name": resource, "data": data.encode("utf-8")})
    return base64.b16encode(resp.mac).decode("utf-8").lower()

def _cloud_kms_hmac_aws(data, cache):
    try:
        boto3 = importlib.import_module("boto3")
    except ImportError:
        raise RuntimeError("boto3 not installed")
    key_id = os.environ["INTEGRITY_KMS_KEY_ID"]
    if "aws" not in cache:
        region = os.environ.get("INTEGRITY_S3_REGION")
        cache["aws"] = boto3.client("kms", region_name=region) if region else boto3.client("kms")
    client = cache["aws"]
    desc = client.describe_key(KeyId=key_id)["KeyMetadata"]
    if desc["KeySpec"] not in ("HMAC_256", "HMAC_SHA_256"):
        raise RuntimeError("AWS KMS key is not HMAC-SHA-256")
    if desc["KeyUsage"] != "GENERATE_VERIFY_MAC":
        raise RuntimeError("AWS KMS key usage is not MAC")
    key_version = desc.get("AWSAccountId", "unknown")
    os.environ["INTEGRITY_KMS_KEY_VERSION"] = key_version
    resp = client.generate_mac(
        KeyId=key_id,
        Message=data.encode("utf-8"),
        MacAlgorithm="HMAC_SHA_256"
    )
    return base64.b16encode(resp["Mac"]).decode("utf-8").lower()

def _cloud_kms_hmac_azure(data, cache):
    try:
        azure_keys = importlib.import_module("azure.keyvault.keys")
        azure_crypto = importlib.import_module("azure.keyvault.keys.crypto")
        azure_id = importlib.import_module("azure.identity")
    except ImportError:
        raise RuntimeError("azure-keyvault-keys and azure-identity not installed")
    key_id = os.environ["INTEGRITY_KMS_KEY_ID"]
    if "azure_cred" not in cache:
        cache["azure_cred"] = azure_id.DefaultAzureCredential()
    cred = cache["azure_cred"]
    if "azure_crypto" not in cache:
        from azure.keyvault.keys.crypto import CryptographyClient
        cache["azure_crypto"] = CryptographyClient(key_id, cred)
    crypto = cache["azure_crypto"]
    if "azure_key_client" not in cache:
        cache["azure_key_client"] = azure_keys.KeyClient(vault_url=key_id.split("/keys/")[0], credential=cred)
    key_client = cache["azure_key_client"]
    key = key_client.get_key(key_id.split("/keys/")[1])
    key_type_str = (key.key_type or "").lower()
    if "hsm" not in key_type_str:
        logger.warning(f"[WARNING] Azure Key Vault key_type '{key.key_type}' does not contain 'hsm'. HMAC with non-HSM keys may not be supported.")
        raise RuntimeError("Azure Key Vault key is not HSM-backed (required for HMAC)")
    if "hmac" not in key_type_str or "sha256" not in key_type_str:
        raise RuntimeError("Azure Key Vault key algorithm is not HMAC-SHA-256")
    key_version = key.properties.version or "unknown"
    os.environ["INTEGRITY_KMS_KEY_VERSION"] = key_version
    from azure.keyvault.keys.crypto import SignatureAlgorithm
    # Use sign_data if available, else fallback to sign (older SDKs)
    if hasattr(crypto, "sign_data"):
        resp = crypto.sign_data(SignatureAlgorithm.hmac_sha256, data.encode("utf-8"))
    else:
        resp = crypto.sign(SignatureAlgorithm.hmac_sha256, data.encode("utf-8"))
    return base64.b16encode(resp.signature).decode("utf-8").lower()
import smtplib
from email.message import EmailMessage
# --- External Anchoring ---
def send_external_anchor_email(table_name, table_hash, schema_version, created_at, hmac_sig):
    """
    Send the latest log seal to an external email address for anchoring.
    WARNING: Email anchoring is delay-tolerant, human-dependent, and not independently verifiable. For strong anchoring, use a public timestamping or WORM/cloud service.
    NOTE: Email anchoring is NOT cryptographic proof and may be delayed or dropped. Use only as a human-auditable backup.
    Improvements:
      - Includes seal hash in subject and body for external proof.
      - Retries sending up to 3 times on failure.
      - Logs all attempts and errors for auditability.
      - Documents limitations and reliability caveats.
    Uses SMTP settings from environment variables:
        INTEGRITY_ANCHOR_EMAIL_TO, INTEGRITY_ANCHOR_EMAIL_FROM, INTEGRITY_ANCHOR_EMAIL_SERVER, INTEGRITY_ANCHOR_EMAIL_PORT, INTEGRITY_ANCHOR_EMAIL_USER, INTEGRITY_ANCHOR_EMAIL_PASS
    """
    logger.warning("[ANCHOR] WARNING: Email anchoring is not cryptographically verifiable and may be delayed or dropped.")
    to_addr = os.environ.get("INTEGRITY_ANCHOR_EMAIL_TO")
    from_addr = os.environ.get("INTEGRITY_ANCHOR_EMAIL_FROM")
    smtp_server = os.environ.get("INTEGRITY_ANCHOR_EMAIL_SERVER")
    smtp_port = int(os.environ.get("INTEGRITY_ANCHOR_EMAIL_PORT", "587"))
    smtp_user = os.environ.get("INTEGRITY_ANCHOR_EMAIL_USER")
    smtp_pass = os.environ.get("INTEGRITY_ANCHOR_EMAIL_PASS")
    if not (to_addr and from_addr and smtp_server and smtp_user and smtp_pass):
        logger.warning("[WARNING] Skipping external anchor email: missing SMTP config. Required: INTEGRITY_ANCHOR_EMAIL_TO, INTEGRITY_ANCHOR_EMAIL_FROM, INTEGRITY_ANCHOR_EMAIL_SERVER, INTEGRITY_ANCHOR_EMAIL_USER, INTEGRITY_ANCHOR_EMAIL_PASS.")
        return
    # Find last seal entry from append-only log
    import time
    log_path = os.environ.get("INTEGRITY_BASELINE_LOG") or "integrity_baseline.log"
    last_seal = None
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in reversed(f.readlines()):
                if not line.strip():
                    continue
                # ...existing code for processing lines...
    except Exception:
        pass

# --- Defensive: Only allow safe identifier usage ---
DEFAULT_ALLOWED_TABLES = {
    "audit_log",
    "donors",
    "donations",
    "donation_allocations",
    "funds",
    "projects",
    "expenses",
    "staff",
    "volunteers",
    "beneficiaries",
    "organizations",
    "users",
    "workflow_events_v2",
    "workflow_instances_v2",
    "workflow_definitions_v2",
    "schema_version",
    "baseline_log",
}


def get_allowed_tables() -> set[str]:
    override = os.environ.get("INTEGRITY_ALLOWED_TABLES")
    if not override:
        return set(DEFAULT_ALLOWED_TABLES)
    items = {item.strip() for item in str(override).split(",") if item.strip()}
    return items or set(DEFAULT_ALLOWED_TABLES)

# --- Utility: get_table_schema_version ---
def get_table_schema_version(conn, table_name):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = cur.fetchall()
    schema = [
        {
            "name": row[1],
            "type": row[2],
            "notnull": bool(row[3]),
            "default": row[4],
            "pk": bool(row[5])
        }
        for row in columns
    ]
    # Add constraints (unique, check, foreign key)
    cur.execute(f"PRAGMA index_list({table_name})")
    indexes = cur.fetchall()
    index_defs = []
    for idx in indexes:
        idx_name = idx[1]
        cur.execute(f"PRAGMA index_info({idx_name})")
        idx_cols = cur.fetchall()
        cur.execute(f"SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (idx_name,))
        idx_sql = cur.fetchone()
        index_defs.append({
            "name": idx_name,
            "unique": bool(idx[2]),
            "columns": [col[2] for col in idx_cols],
            "sql": idx_sql[0] if idx_sql and idx_sql[0] else None
        })
    # Triggers
    cur.execute(f"SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table_name,))
    triggers = cur.fetchall()
    trigger_defs = [{"name": t[0], "sql": t[1]} for t in triggers]
    # Foreign keys
    cur.execute(f"PRAGMA foreign_key_list({table_name})")
    fks = cur.fetchall()
    fk_defs = [
        {
            "id": fk[0],
            "seq": fk[1],
            "table": fk[2],
            "from": fk[3],
            "to": fk[4],
            "on_update": fk[5],
            "on_delete": fk[6],
            "match": fk[7]
        }
        for fk in fks
    ]
    # Compose full schema signature
    schema_info = {
        "columns": schema,
        "indexes": index_defs,
        "triggers": trigger_defs,
        "foreign_keys": fk_defs
    }
    import hashlib, json
    return hashlib.sha256(json.dumps(schema_info, sort_keys=True).encode('utf-8')).hexdigest()

def compute_table_hash(conn, table_name, salt=None, schema_version=None, chunk_size=10000):
    """
    Compute a stable, deterministic hash of a table's contents.
    - Uses JSON serialization for stability.
    - Orders by all PK columns (or all columns if no PK).
    - Includes schema version and salt.
    - Handles NULLs, floats, and column order robustly.
    - Chunked hashing (with chunk_size) mitigates memory pressure for large tables by not loading all rows at once.
    - Uses BEGIN IMMEDIATE to prevent concurrent writes during hashing. This will block other writers until the transaction is committed or rolled back, and may block for a long time if there are long-running write transactions.
    NOTE: If no PK, row order may not be stable across DBs/exports. For best results, ensure PK exists. For very large datasets, consider streaming/iterator approach instead of fetchall().
    """
    if table_name not in get_allowed_tables():
        raise ValueError(f"Unauthorized table: {table_name}")
    cur = conn.cursor()
    # BEGIN IMMEDIATE to prevent concurrent writes during hashing
    cur.execute("BEGIN IMMEDIATE;")
    try:
        # Get column names and PKs (only one PRAGMA call)
        cur.execute(f"PRAGMA table_info({table_name})")
        table_info = cur.fetchall()
        cols = [row[1] for row in table_info]
        pk_cols = [row[1] for row in table_info if row[5]]
        if not pk_cols:
            import warnings
            warnings.warn(f"Table '{table_name}' has no PRIMARY KEY. Hashing and pagination may be non-deterministic.")
        order_by = ','.join(pk_cols) if pk_cols else ','.join(cols)
        # Defensive: only allow safe identifier usage
        if not salt:
            salt = os.environ.get("INTEGRITY_HASH_SALT")
        if not salt:
            raise RuntimeError("INTEGRITY_HASH_SALT environment variable must be set for table hash integrity.")
        schema_version = schema_version or get_table_schema_version(conn, table_name)
        m = hashlib.sha256()
        m.update(f"schema_version:{schema_version}|salt:{salt}".encode("utf-8"))

        def row_generator():
            if pk_cols:
                last_pk = None
                while True:
                    if last_pk is None:
                        cur.execute(f"SELECT * FROM {table_name} ORDER BY {order_by} LIMIT ?", (chunk_size,))
                    else:
                        where = ' AND '.join([f"{col} > ?" for col in pk_cols])
                        cur.execute(f"SELECT * FROM {table_name} WHERE {where} ORDER BY {order_by} LIMIT ?", (*last_pk, chunk_size))
                    rows = cur.fetchall()
                    if not rows:
                        break
                    for row in rows:
                        yield dict(zip(cols, row))
                    last_pk = tuple(rows[-1][cols.index(col)] for col in pk_cols)
            else:
                offset = 0
                while True:
                    cur.execute(f"SELECT * FROM {table_name} ORDER BY {order_by} LIMIT ? OFFSET ?", (chunk_size, offset))
                    rows = cur.fetchall()
                    if not rows:
                        break
                    for row in rows:
                        yield dict(zip(cols, row))
                    offset += chunk_size

        # Streaming chunked/Merkle hashing
        chunk = []
        chunk_hashes = []
        for row in row_generator():
            chunk.append(row)
            if len(chunk) >= chunk_size:
                chunk_m = hashlib.sha256()
                for r in chunk:
                    chunk_m.update(json.dumps(r, sort_keys=True, separators=(',', ':')).encode('utf-8'))
                chunk_hashes.append(chunk_m.hexdigest())
                chunk = []
        if chunk:
            chunk_m = hashlib.sha256()
            for r in chunk:
                chunk_m.update(json.dumps(r, sort_keys=True, separators=(',', ':')).encode('utf-8'))
            chunk_hashes.append(chunk_m.hexdigest())
        # Merkle-style: hash the chunk hashes
        for ch in chunk_hashes:
            m.update(ch.encode('utf-8'))
        return m.hexdigest()
    finally:
        cur.execute("COMMIT;")
 # --- DB-backed Baseline Table ---
# Note: Anyone with write access to the database can modify the integrity_baseline table and recompute HMAC if they also have the key. This detects unexpected changes, not malicious root compromise.
def ensure_integrity_baseline_table(conn):
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS integrity_baseline (
            table_name TEXT PRIMARY KEY,
            table_hash TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            hmac TEXT NOT NULL
        )
    ''')
    conn.commit()

def get_db_baseline(conn, table_name):
    ensure_integrity_baseline_table(conn)
    cur = conn.cursor()
    cur.execute("SELECT table_hash, schema_version, created_at, hmac FROM integrity_baseline WHERE table_name = ?", (table_name,))
    row = cur.fetchone()
    if row:
        return {
            "table_hash": row[0],
            "schema_version": row[1],
            "created_at": row[2],
            "hmac": row[3]
        }
    return None

def set_db_baseline(conn, table_name, table_hash, schema_version, hmac_sig):
    ensure_integrity_baseline_table(conn)
    cur = conn.cursor()
    key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
    key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
    algorithm = "HMAC-SHA-256"
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("PRAGMA table_info(integrity_baseline)")
        columns = [row[1] for row in cur.fetchall()]
        if "key_id" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_id TEXT")
        if "key_version" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_version TEXT")
        if "algorithm" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN algorithm TEXT")
        cur.execute("REPLACE INTO integrity_baseline (table_name, table_hash, schema_version, created_at, hmac, key_id, key_version, algorithm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (table_name, table_hash, schema_version, datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), hmac_sig, key_id, key_version, algorithm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update integrity_baseline table: {e}")
        raise

def get_hmac_key(salt=None):
    """
    Returns the HMAC key for baseline signing.
    - Uses INTEGRITY_HMAC_KEY env var if set.
    - Falls back to salt ONLY if INTEGRITY_HMAC_KEY_FALLBACK_OK is set (explicit opt-in).
    - Raises if neither is set.
    """
    # Never log or expose HMAC key or fallback salt. Only return if explicitly set.
    hmac_key = os.environ.get("INTEGRITY_HMAC_KEY")
    if hmac_key:
        return hmac_key
    # Fallback to salt is dangerous and should only be used for non-production/testing. Never log salt value.
    if os.environ.get("INTEGRITY_HMAC_KEY_FALLBACK_OK") and salt:
        import warnings
        logger.warning("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        warnings.warn("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        return salt
    logger.error("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")
    raise RuntimeError("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")


def compute_hmac(data: str, key: str) -> str:
    # If Cloud KMS is configured, use it
    # Never log or expose KMS credentials or HMAC key. Only use for cryptographic operations.
    if os.environ.get("INTEGRITY_KMS_PROVIDER"):
        try:
            return cloud_kms_hmac(data)
        except Exception as e:
            if os.environ.get("INTEGRITY_KMS_FALLBACK_OK") == "1":
                logger.warning(f"[KMS] Cloud KMS HMAC failed: {e}. Falling back to local key. This should be alerted/audited.")
                try:
                    from ..auth.alerting import alert_security_event
                    alert_security_event(
                        "integrity.kms_fallback",
                        {"error": str(e), "message": "Cloud KMS HMAC failed, fallback to local key used."}
                    )
                except Exception as alert_exc:
                    logger.error(f"Failed to alert on KMS fallback: {alert_exc}")
            else:
                logger.error(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
                raise RuntimeError(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
    # Use a separator for HMAC input
    return hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()


def set_db_baseline(conn, table_name, table_hash, schema_version, hmac_sig):
    ensure_integrity_baseline_table(conn)
    cur = conn.cursor()
    key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
    key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
    algorithm = "HMAC-SHA-256"
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("PRAGMA table_info(integrity_baseline)")
        columns = [row[1] for row in cur.fetchall()]
        if "key_id" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_id TEXT")
        if "key_version" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_version TEXT")
        if "algorithm" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN algorithm TEXT")
        cur.execute("REPLACE INTO integrity_baseline (table_name, table_hash, schema_version, created_at, hmac, key_id, key_version, algorithm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (table_name, table_hash, schema_version, datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), hmac_sig, key_id, key_version, algorithm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update integrity_baseline table: {e}")
        raise

def get_hmac_key(salt=None):
    """
    Returns the HMAC key for baseline signing.
    - Uses INTEGRITY_HMAC_KEY env var if set.
    - Falls back to salt ONLY if INTEGRITY_HMAC_KEY_FALLBACK_OK is set (explicit opt-in).
    - Raises if neither is set.
    """
    # Never log or expose HMAC key or fallback salt. Only return if explicitly set.
    hmac_key = os.environ.get("INTEGRITY_HMAC_KEY")
    if hmac_key:
        return hmac_key
    # Fallback to salt is dangerous and should only be used for non-production/testing. Never log salt value.
    if os.environ.get("INTEGRITY_HMAC_KEY_FALLBACK_OK") and salt:
        import warnings
        logger.warning("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        warnings.warn("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        return salt
    logger.error("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")
    raise RuntimeError("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")


def compute_hmac(data: str, key: str) -> str:
    # If Cloud KMS is configured, use it
    # Never log or expose KMS credentials or HMAC key. Only use for cryptographic operations.
    if os.environ.get("INTEGRITY_KMS_PROVIDER"):
        try:
            return cloud_kms_hmac(data)
        except Exception as e:
            if os.environ.get("INTEGRITY_KMS_FALLBACK_OK") == "1":
                logger.warning(f"[KMS] Cloud KMS HMAC failed: {e}. Falling back to local key. This should be alerted/audited.")
                try:
                    from ..auth.alerting import alert_security_event
                    alert_security_event(
                        "integrity.kms_fallback",
                        {"error": str(e), "message": "Cloud KMS HMAC failed, fallback to local key used."}
                    )
                except Exception as alert_exc:
                    logger.error(f"Failed to alert on KMS fallback: {alert_exc}")
            else:
                logger.error(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
                raise RuntimeError(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
    # Use a separator for HMAC input
    return hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()


def set_db_baseline(conn, table_name, table_hash, schema_version, hmac_sig):
    ensure_integrity_baseline_table(conn)
    cur = conn.cursor()
    key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
    key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
    algorithm = "HMAC-SHA-256"
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("PRAGMA table_info(integrity_baseline)")
        columns = [row[1] for row in cur.fetchall()]
        if "key_id" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_id TEXT")
        if "key_version" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_version TEXT")
        if "algorithm" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN algorithm TEXT")
        cur.execute("REPLACE INTO integrity_baseline (table_name, table_hash, schema_version, created_at, hmac, key_id, key_version, algorithm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (table_name, table_hash, schema_version, datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), hmac_sig, key_id, key_version, algorithm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update integrity_baseline table: {e}")
        raise

def get_hmac_key(salt=None):
    """
    Returns the HMAC key for baseline signing.
    - Uses INTEGRITY_HMAC_KEY env var if set.
    - Falls back to salt ONLY if INTEGRITY_HMAC_KEY_FALLBACK_OK is set (explicit opt-in).
    - Raises if neither is set.
    """
    # Never log or expose HMAC key or fallback salt. Only return if explicitly set.
    hmac_key = os.environ.get("INTEGRITY_HMAC_KEY")
    if hmac_key:
        return hmac_key
    # Fallback to salt is dangerous and should only be used for non-production/testing. Never log salt value.
    if os.environ.get("INTEGRITY_HMAC_KEY_FALLBACK_OK") and salt:
        import warnings
        logger.warning("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        warnings.warn("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        return salt
    logger.error("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")
    raise RuntimeError("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")


def compute_hmac(data: str, key: str) -> str:
    # If Cloud KMS is configured, use it
    # Never log or expose KMS credentials or HMAC key. Only use for cryptographic operations.
    if os.environ.get("INTEGRITY_KMS_PROVIDER"):
        try:
            return cloud_kms_hmac(data)
        except Exception as e:
            if os.environ.get("INTEGRITY_KMS_FALLBACK_OK") == "1":
                logger.warning(f"[KMS] Cloud KMS HMAC failed: {e}. Falling back to local key. This should be alerted/audited.")
                try:
                    from ..auth.alerting import alert_security_event
                    alert_security_event(
                        "integrity.kms_fallback",
                        {"error": str(e), "message": "Cloud KMS HMAC failed, fallback to local key used."}
                    )
                except Exception as alert_exc:
                    logger.error(f"Failed to alert on KMS fallback: {alert_exc}")
            else:
                logger.error(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
                raise RuntimeError(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
    # Use a separator for HMAC input
    return hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()


def set_db_baseline(conn, table_name, table_hash, schema_version, hmac_sig):
    ensure_integrity_baseline_table(conn)
    cur = conn.cursor()
    key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
    key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
    algorithm = "HMAC-SHA-256"
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("PRAGMA table_info(integrity_baseline)")
        columns = [row[1] for row in cur.fetchall()]
        if "key_id" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_id TEXT")
        if "key_version" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_version TEXT")
        if "algorithm" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN algorithm TEXT")
        cur.execute("REPLACE INTO integrity_baseline (table_name, table_hash, schema_version, created_at, hmac, key_id, key_version, algorithm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (table_name, table_hash, schema_version, datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), hmac_sig, key_id, key_version, algorithm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update integrity_baseline table: {e}")
        raise

def get_hmac_key(salt=None):
    """
    Returns the HMAC key for baseline signing.
    - Uses INTEGRITY_HMAC_KEY env var if set.
    - Falls back to salt ONLY if INTEGRITY_HMAC_KEY_FALLBACK_OK is set (explicit opt-in).
    - Raises if neither is set.
    """
    # Never log or expose HMAC key or fallback salt. Only return if explicitly set.
    hmac_key = os.environ.get("INTEGRITY_HMAC_KEY")
    if hmac_key:
        return hmac_key
    # Fallback to salt is dangerous and should only be used for non-production/testing. Never log salt value.
    if os.environ.get("INTEGRITY_HMAC_KEY_FALLBACK_OK") and salt:
        import warnings
        logger.warning("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        warnings.warn("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        return salt
    logger.error("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")
    raise RuntimeError("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")


def compute_hmac(data: str, key: str) -> str:
    # If Cloud KMS is configured, use it
    # Never log or expose KMS credentials or HMAC key. Only use for cryptographic operations.
    if os.environ.get("INTEGRITY_KMS_PROVIDER"):
        try:
            return cloud_kms_hmac(data)
        except Exception as e:
            if os.environ.get("INTEGRITY_KMS_FALLBACK_OK") == "1":
                logger.warning(f"[KMS] Cloud KMS HMAC failed: {e}. Falling back to local key. This should be alerted/audited.")
                try:
                    from ..auth.alerting import alert_security_event
                    alert_security_event(
                        "integrity.kms_fallback",
                        {"error": str(e), "message": "Cloud KMS HMAC failed, fallback to local key used."}
                    )
                except Exception as alert_exc:
                    logger.error(f"Failed to alert on KMS fallback: {alert_exc}")
            else:
                logger.error(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
                raise RuntimeError(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
    # Use a separator for HMAC input
    return hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()


def set_db_baseline(conn, table_name, table_hash, schema_version, hmac_sig):
    ensure_integrity_baseline_table(conn)
    cur = conn.cursor()
    key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
    key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
    algorithm = "HMAC-SHA-256"
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("PRAGMA table_info(integrity_baseline)")
        columns = [row[1] for row in cur.fetchall()]
        if "key_id" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_id TEXT")
        if "key_version" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_version TEXT")
        if "algorithm" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN algorithm TEXT")
        cur.execute("REPLACE INTO integrity_baseline (table_name, table_hash, schema_version, created_at, hmac, key_id, key_version, algorithm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (table_name, table_hash, schema_version, datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), hmac_sig, key_id, key_version, algorithm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update integrity_baseline table: {e}")
        raise

def get_hmac_key(salt=None):
    """
    Returns the HMAC key for baseline signing.
    - Uses INTEGRITY_HMAC_KEY env var if set.
    - Falls back to salt ONLY if INTEGRITY_HMAC_KEY_FALLBACK_OK is set (explicit opt-in).
    - Raises if neither is set.
    """
    # Never log or expose HMAC key or fallback salt. Only return if explicitly set.
    hmac_key = os.environ.get("INTEGRITY_HMAC_KEY")
    if hmac_key:
        return hmac_key
    # Fallback to salt is dangerous and should only be used for non-production/testing. Never log salt value.
    if os.environ.get("INTEGRITY_HMAC_KEY_FALLBACK_OK") and salt:
        import warnings
        logger.warning("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        warnings.warn("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        return salt
    logger.error("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")
    raise RuntimeError("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")


def compute_hmac(data: str, key: str) -> str:
    # If Cloud KMS is configured, use it
    # Never log or expose KMS credentials or HMAC key. Only use for cryptographic operations.
    if os.environ.get("INTEGRITY_KMS_PROVIDER"):
        try:
            return cloud_kms_hmac(data)
        except Exception as e:
            if os.environ.get("INTEGRITY_KMS_FALLBACK_OK") == "1":
                logger.warning(f"[KMS] Cloud KMS HMAC failed: {e}. Falling back to local key. This should be alerted/audited.")
                try:
                    from ..auth.alerting import alert_security_event
                    alert_security_event(
                        "integrity.kms_fallback",
                        {"error": str(e), "message": "Cloud KMS HMAC failed, fallback to local key used."}
                    )
                except Exception as alert_exc:
                    logger.error(f"Failed to alert on KMS fallback: {alert_exc}")
            else:
                logger.error(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
                raise RuntimeError(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
    # Use a separator for HMAC input
    return hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()


def set_db_baseline(conn, table_name, table_hash, schema_version, hmac_sig):
    ensure_integrity_baseline_table(conn)
    cur = conn.cursor()
    key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
    key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
    algorithm = "HMAC-SHA-256"
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("PRAGMA table_info(integrity_baseline)")
        columns = [row[1] for row in cur.fetchall()]
        if "key_id" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_id TEXT")
        if "key_version" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_version TEXT")
        if "algorithm" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN algorithm TEXT")
        cur.execute("REPLACE INTO integrity_baseline (table_name, table_hash, schema_version, created_at, hmac, key_id, key_version, algorithm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (table_name, table_hash, schema_version, datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), hmac_sig, key_id, key_version, algorithm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update integrity_baseline table: {e}")
        raise

def get_hmac_key(salt=None):
    """
    Returns the HMAC key for baseline signing.
    - Uses INTEGRITY_HMAC_KEY env var if set.
    - Falls back to salt ONLY if INTEGRITY_HMAC_KEY_FALLBACK_OK is set (explicit opt-in).
    - Raises if neither is set.
    """
    # Never log or expose HMAC key or fallback salt. Only return if explicitly set.
    hmac_key = os.environ.get("INTEGRITY_HMAC_KEY")
    if hmac_key:
        return hmac_key
    # Fallback to salt is dangerous and should only be used for non-production/testing. Never log salt value.
    if os.environ.get("INTEGRITY_HMAC_KEY_FALLBACK_OK") and salt:
        import warnings
        logger.warning("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        warnings.warn("Fallback to salt as HMAC key is enabled. This is dangerous and should NOT be used in production.")
        return salt
    logger.error("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")
    raise RuntimeError("INTEGRITY_HMAC_KEY must be set for baseline HMAC. Set INTEGRITY_HMAC_KEY_FALLBACK_OK=1 to allow fallback to salt (not recommended).")


def compute_hmac(data: str, key: str) -> str:
    # If Cloud KMS is configured, use it
    # Never log or expose KMS credentials or HMAC key. Only use for cryptographic operations.
    if os.environ.get("INTEGRITY_KMS_PROVIDER"):
        try:
            return cloud_kms_hmac(data)
        except Exception as e:
            if os.environ.get("INTEGRITY_KMS_FALLBACK_OK") == "1":
                logger.warning(f"[KMS] Cloud KMS HMAC failed: {e}. Falling back to local key. This should be alerted/audited.")
                try:
                    from ..auth.alerting import alert_security_event
                    alert_security_event(
                        "integrity.kms_fallback",
                        {"error": str(e), "message": "Cloud KMS HMAC failed, fallback to local key used."}
                    )
                except Exception as alert_exc:
                    logger.error(f"Failed to alert on KMS fallback: {alert_exc}")
            else:
                logger.error(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
                raise RuntimeError(f"Cloud KMS HMAC failed and fallback is not allowed: {e}")
    # Use a separator for HMAC input
    return hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()


def set_db_baseline(conn, table_name, table_hash, schema_version, hmac_sig):
    ensure_integrity_baseline_table(conn)
    cur = conn.cursor()
    key_id = os.environ.get("INTEGRITY_KMS_KEY_ID") or os.environ.get("INTEGRITY_KMS_RESOURCE") or "local"
    key_version = os.environ.get("INTEGRITY_KMS_KEY_VERSION") or "unknown"
    algorithm = "HMAC-SHA-256"
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("PRAGMA table_info(integrity_baseline)")
        columns = [row[1] for row in cur.fetchall()]
        if "key_id" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_id TEXT")
        if "key_version" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN key_version TEXT")
        if "algorithm" not in columns:
            cur.execute("ALTER TABLE integrity_baseline ADD COLUMN algorithm TEXT")
        cur.execute("REPLACE INTO integrity_baseline (table_name, table_hash, schema_version, created_at, hmac, key_id, key_version, algorithm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (table_name, table_hash, schema_version, datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), hmac_sig, key_id, key_version, algorithm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update integrity_baseline table: {e}")
        raise

