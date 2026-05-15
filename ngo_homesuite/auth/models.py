# --- Canonical Roles (single source of truth) ---
from __future__ import annotations

# --- Canonical Roles (single source of truth) ---
CANONICAL_ROLES = {"admin", "fundraiser", "viewer"}

# --- Audit PII Policy ---
# WARNING: Audit logs may contain usernames and IP addresses. Ensure compliance with GDPR/PII policies for storage, retention, and access.


import sqlite3

def login_user(conn: sqlite3.Connection, cur: sqlite3.Cursor, username: str, password: str, ip_address: str | None = None) -> bool:
    """
    Attempt to log in a user, enforcing lockout and rate-limiting.
    Returns True if login is successful, False otherwise.
    Raises ValueError for lockout or other user-facing errors.
    """
    # Retry logic for SQLite database lock errors
    import sqlite3 as _sqlite3
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Ensure tables exist
            ensure_login_attempts_table(conn, cur)
            ensure_hibp_failures_table(cur)
            # Check lockout
            locked, lockout_until = is_account_locked(cur, username)
            if locked:
                audit("auth.login.locked_out", entity_type="user", details={"username": username, "lockout_until": lockout_until, "ip_address": ip_address})
                conn.rollback()
                raise ValueError(f"Account is temporarily locked due to repeated failed login attempts. Try again after {lockout_until}.")

            # Fetch user hash
            cur.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
            row = cur.fetchone()
            if not row:
                record_failed_login(conn, cur, username, ip_address)
                audit("auth.login.failed", entity_type="user", details={"username": username, "reason": "no_such_user", "ip_address": ip_address})
                conn.commit()
                raise ValueError("Invalid username or password.")
            expected_hash = row[0]

            is_valid, needs_rehash = verify_password(password, expected_hash)
            if not is_valid:
                record_failed_login(conn, cur, username, ip_address)
                audit("auth.login.failed", entity_type="user", details={"username": username, "reason": "bad_password", "ip_address": ip_address})
                conn.commit()
                raise ValueError("Invalid username or password.")

            # Success: reset attempts
            record_successful_login(conn, cur, username)
            audit("auth.login.success", entity_type="user", details={"username": username, "ip_address": ip_address})

            # Rehash-on-login if needed
            if needs_rehash:
                new_hash = ARGON2_PH.hash(password)
                cur.execute("UPDATE users SET password_hash = ? WHERE username = ?", (new_hash, username))
                audit("auth.password.rehash", entity_type="user", details={"username": username})

            conn.commit()
            return True
        except _sqlite3.OperationalError as e:
            conn.rollback()
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))
                continue
            else:
                raise
# --- Login Attempt Management ---
import datetime
from datetime import timedelta

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_BASE_MINUTES = 15
MAX_LOCKOUT_MINUTES = 1440  # 24 hours

def _now_iso():
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()

def get_login_attempt(cur: sqlite3.Cursor, username: str) -> dict | None:
    cur.execute("SELECT username, failed_attempts, last_attempt_ts, lockout_until, ip_address FROM login_attempts WHERE username = ?", (username,))
    row = cur.fetchone()
    if row:
        return dict(zip(["username", "failed_attempts", "last_attempt_ts", "lockout_until", "ip_address"], row))
    return None

def set_login_attempt(cur: sqlite3.Cursor, username: str, failed_attempts: int, last_attempt_ts: str, lockout_until: str | None, ip_address: str | None = None):
    cur.execute("""
        INSERT INTO login_attempts (username, failed_attempts, last_attempt_ts, lockout_until, ip_address)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            failed_attempts=excluded.failed_attempts,
            last_attempt_ts=excluded.last_attempt_ts,
            lockout_until=excluded.lockout_until,
            ip_address=excluded.ip_address
    """, (username, failed_attempts, last_attempt_ts, lockout_until, ip_address))

def reset_login_attempt(cur: sqlite3.Cursor, username: str):
    cur.execute("DELETE FROM login_attempts WHERE username = ?", (username,))

def is_account_locked(cur: sqlite3.Cursor, username: str) -> tuple[bool, str | None]:
    attempt = get_login_attempt(cur, username)
    if attempt and attempt["lockout_until"]:
        try:
            until = datetime.datetime.fromisoformat(attempt["lockout_until"])
            if until > datetime.datetime.now(datetime.UTC):
                return True, attempt["lockout_until"]
        except Exception:
            pass
    return False, None

def record_failed_login(conn: sqlite3.Connection, cur: sqlite3.Cursor, username: str, ip_address: str | None = None):
    attempt = get_login_attempt(cur, username)
    now = _now_iso()
    fails = 1
    lockout_until = None
    if attempt:
        # Sliding window: decay failed_attempts if last attempt was >1 hour ago
        last_ts = attempt["last_attempt_ts"]
        if last_ts:
            try:
                last_dt = datetime.datetime.fromisoformat(last_ts)
                if (datetime.datetime.now(datetime.UTC) - last_dt).total_seconds() > 3600:
                    fails = 1
                else:
                    fails = attempt["failed_attempts"] + 1
            except Exception:
                fails = attempt["failed_attempts"] + 1
        else:
            fails = attempt["failed_attempts"] + 1
        lockout_minutes = min(LOCKOUT_BASE_MINUTES * (2 ** max(0, fails - MAX_FAILED_ATTEMPTS)), MAX_LOCKOUT_MINUTES)
        if fails >= MAX_FAILED_ATTEMPTS:
            lockout_until = (datetime.datetime.now(datetime.UTC) + timedelta(minutes=lockout_minutes)).replace(microsecond=0).isoformat()
            audit("auth.lockout", entity_type="user", details={"username": username, "lockout_until": lockout_until, "fail_count": fails})
    set_login_attempt(cur, username, fails, now, lockout_until, ip_address)
    conn.commit()

def record_successful_login(conn: sqlite3.Connection, cur: sqlite3.Cursor, username: str):
    reset_login_attempt(cur, username)
    conn.commit()
# --- Login Attempts & Lockout Schema ---
def ensure_login_attempts_table(conn: sqlite3.Connection, cur: sqlite3.Cursor) -> None:
    """
    Ensure the login_attempts table exists for tracking failed logins and lockouts.
    Fields:
      - username: str (unique)
      - failed_attempts: int
      - last_attempt_ts: ISO8601 timestamp
      - lockout_until: ISO8601 timestamp (nullable)
      - ip_address: str (optional, nullable)
    """
    cur.execute('''
        CREATE TABLE IF NOT EXISTS login_attempts (
            username TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_ts TEXT,
            lockout_until TEXT,
            ip_address TEXT
        )
    ''')
    conn.commit()

import getpass
import hashlib
import sqlite3
from typing import Any
import os
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
from zxcvbn import zxcvbn
import requests
from requests import RequestException
from ..prompts import prompt_non_empty, utc_now_iso
from ..db.connection import run_db
from .session import AuthError, CURRENT_USER, require_role
from ..db.utils import audit

# --- Argon2id configuration (static at import, with validation) ---
def _get_env_int(name: str, default: int, minval: int, maxval: int) -> int:
    val = os.environ.get(name)
    try:
        ival = int(val) if val is not None else default
        if ival < minval or ival > maxval:
            print(f"[SECURITY WARNING] {name}={ival} is out of safe range [{minval}, {maxval}]. Using default {default}.")
            return default
        return ival
    except Exception:
        print(f"[SECURITY WARNING] {name}={val} is not a valid integer. Using default {default}.")
        return default

# OWASP minimums: time_cost >= 2, memory_cost >= 19456 (19MiB), parallelism >= 1
ARGON2_TIME_COST = _get_env_int("ARGON2_TIME_COST", 3, 2, 10)
ARGON2_MEMORY_COST = _get_env_int("ARGON2_MEMORY_COST", 131072, 19456, 1048576)
ARGON2_PARALLELISM = _get_env_int("ARGON2_PARALLELISM", 1, 1, 8)
ARGON2_HASH_LEN = _get_env_int("ARGON2_HASH_LEN", 32, 16, 128)
ARGON2_SALT_LEN = _get_env_int("ARGON2_SALT_LEN", 16, 8, 64)
ARGON2_PH = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
    type=Type.ID
)


def _enforce_password_policy(username: str, password: str, role: str = None) -> None:
    """
    Enforces password policy: min length, zxcvbn entropy, pwned check.
    WARNING: zxcvbn is slow and may be a DoS risk if called in tight loops or on untrusted input at scale.
    NOTE: No unit tests for this function or _check_pwned are included; edge cases (e.g., password contains username/role) should be tested.
    """
    if not password:
        raise ValueError("Password cannot be empty.")
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters long.")
    user_inputs = [username]
    if role:
        user_inputs.append(role)
    # --- Async/caching stub for zxcvbn ---
    # For high-volume environments, consider using an async or cached version of zxcvbn.
    # Example stub:
    # async def zxcvbn_async(password, user_inputs=None):
    #     # Implement async or cache logic here
    #     return zxcvbn(password, user_inputs=user_inputs)
    score = zxcvbn(password, user_inputs=user_inputs)['score']
    if score < 4:
        raise ValueError("Password is too weak. Try a longer or more complex phrase.")
    # --- Async/caching stub for HIBP ---
    # For high-volume environments, consider using an async or cached version of the HIBP check.
    # Example stub:
    # async def check_pwned_async(password, username=None, role=None, cur=None):
    #     # Implement async or cache logic here
    #     return _check_pwned(password, username, role, cur=cur)
    pwned_result = _check_pwned(password, username, role)
    if pwned_result is True:
        audit("password.pwned_breach", entity_type="user", details={"username": username, "role": role})
        raise ValueError("Sorry, that password has appeared in a public breach. Please choose another.")
    elif pwned_result == "hibp_unavailable":
        audit("password.pwned_api_error", entity_type="user", details={"username": username, "role": role, "error": "HIBP unavailable, uniform error"})
        # Fail open: allow creation, but log error and alert admin if repeated failures
        _alert_admin_hibp_failure()
        pass

# --- Password Verification ---
def verify_password(password: str, expected_hash: str) -> tuple[bool, bool]:
    # Example usage for rehash-on-login (recommended pattern):
    # is_valid, needs_rehash = verify_password(password, hash)
    # if is_valid and needs_rehash:
    #     # Perform rehash and update in the login flow, not here:
    #     new_hash = ARGON2_PH.hash(password)
    #     update_user_hash_in_db(username, new_hash)
    """
    Returns (is_valid, needs_rehash). Always returns a tuple of bools.

    Note: This function only signals if a rehash is needed. The actual rehash and DB update
    should be performed by the caller (e.g., in the login flow), not here. This is the standard pattern.
    """
    try:
        valid = ARGON2_PH.verify(expected_hash, password)
        try:
            needs_rehash = ARGON2_PH.check_needs_rehash(expected_hash)
        except Exception:
            needs_rehash = True
        return valid, needs_rehash
    except VerifyMismatchError:
        return False, False

# --- User Creation ---
def create_user(conn: sqlite3.Connection, cur: sqlite3.Cursor, username: str, password: str, role: str) -> None:
    role = role.strip().lower()
    if role not in CANONICAL_ROLES:
        audit("user.create.failed", entity_type="user", details={
            "username": username,
            "reason": "invalid_role",
            "role": role
        })
        raise ValueError("Invalid role")

    username = username.strip()
    if not username:
        audit("user.create.failed", entity_type="user", details={
            "username": username,
            "reason": "empty_username",
            "role": role
        })
        raise ValueError("Username is required")


    try:
        _enforce_password_policy(username, password, role)
    except ValueError as e:
        audit("user.create.failed", entity_type="user", details={
            "username": username,
            "reason": "password_policy",
            "role": role,
            "error": str(e)
        })
        raise

    pw_hash = ARGON2_PH.hash(password)

    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, pw_hash, role, utc_now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        audit("user.create.failed", entity_type="user", details={
            "username": username,
            "reason": "db_integrity_error",
            "role": role,
            "error": str(e)
        })
        if "UNIQUE constraint failed: users.username" in str(e):
            raise ValueError("A user with that username already exists.")
        else:
            raise ValueError("Could not create user due to a database constraint error.")
    except Exception as e:
        conn.rollback()
        audit("user.create.failed", entity_type="user", details={
            "username": username,
            "reason": "db_error",
            "role": role,
            "error": str(e)
        })
        raise

    audit("user.create", entity_type="user", details={"username": username, "role": role})



import time
_HIBP_FAILURE_THRESHOLD = 5

# --- Persistent HIBP failure count (DB-backed) ---

# --- HIBP Failures Table Management ---
def ensure_hibp_failures_table(cur: sqlite3.Cursor):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hibp_failures (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            count INTEGER NOT NULL DEFAULT 0,
            last_failure_ts TEXT
        )
    """)
    cur.execute("INSERT OR IGNORE INTO hibp_failures (id, count, last_failure_ts) VALUES (1, 0, NULL)")

def _get_hibp_failure_count(cur: sqlite3.Cursor) -> int:
    ensure_hibp_failures_table(cur)
    cur.execute("SELECT count FROM hibp_failures WHERE id = 1")
    return int((cur.fetchone() or [0])[0] or 0)

def _set_hibp_failure_count(cur: sqlite3.Cursor, count: int):
    ensure_hibp_failures_table(cur)
    cur.execute("UPDATE hibp_failures SET count = ?, last_failure_ts = CURRENT_TIMESTAMP WHERE id = 1", (count,))

# Race-safe increment for HIBP failure count, with timestamp
def _inc_hibp_failure_count(cur: sqlite3.Cursor, by: int = 1):
    ensure_hibp_failures_table(cur)
    cur.execute("UPDATE hibp_failures SET count = count + ?, last_failure_ts = CURRENT_TIMESTAMP WHERE id = 1", (by,))

# Reset HIBP failure count and timestamp
def _reset_hibp_failure_count(cur: sqlite3.Cursor):
    ensure_hibp_failures_table(cur)
    cur.execute("UPDATE hibp_failures SET count = 0, last_failure_ts = NULL WHERE id = 1")

def _check_pwned(password: str, username: str = None, role: str = None, retries: int = 2, timeout: int = 3, cur: sqlite3.Cursor = None) -> bool | str:
    """
    Checks if the password has been pwned using the HIBP API.
    Returns True if pwned, False if not. Raises RuntimeError if the API is unreachable or errors occur.
    No SHA1 or password data is logged or returned.
    NOTE: For high-volume environments, consider async or caching for HIBP/zxcvbn to avoid blocking.
    """
    # Accepts either a conn or cur for DB operations
    # Always fail open: never raise, just audit and alert
    try:
        if cur is not None:
            ensure_hibp_failures_table(cur)
        sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        prefix, suffix = sha1[:5], sha1[5:]
        url = f"https://api.pwnedpasswords.com/range/{prefix}"
        headers = {
            "User-Agent": "ngo-homesuite/1.0 (contact: security@example.com)",
            "Add-Padding": "true"
        }
        max_total_time = 10  # seconds
        start_time = time.monotonic()
        for attempt in range(retries + 1):
            try:
                resp = requests.get(url, timeout=timeout, headers=headers)
                if resp.status_code != 200:
                    if cur is not None:
                        _inc_hibp_failure_count(cur, 1)
                    audit("password.pwned_api_error", entity_type="user", details={"username": username, "role": role, "error": f"HIBP status {resp.status_code}"})
                    _alert_admin_hibp_failure(cur)
                    return "hibp_unavailable"
                hashes = (line.split(":") for line in resp.text.splitlines())
                if any(h[0] == suffix for h in hashes):
                    if cur is not None:
                        _reset_hibp_failure_count(cur)
                    return True
                if cur is not None:
                    _reset_hibp_failure_count(cur)
                return False
            except RequestException as ex:
                if cur is not None:
                    _inc_hibp_failure_count(cur, 1)
                audit("password.pwned_api_error", entity_type="user", details={"username": username, "role": role, "error": str(ex)})
                elapsed = time.monotonic() - start_time
                remaining = max_total_time - elapsed
                if remaining <= 0:
                    break
                next_wait = min(0.5 * (2 ** attempt), 3.0, remaining)
                if next_wait <= 0:
                    break
                time.sleep(next_wait)
            if (time.monotonic() - start_time) > max_total_time:
                break
        # If all attempts fail, fail open
        _alert_admin_hibp_failure(cur)
        return "hibp_unavailable"
    except Exception as ex:
        # Defensive: never raise, always fail open
        audit("password.pwned_api_error", entity_type="user", details={"username": username, "role": role, "error": f"internal error: {ex}"})
        _alert_admin_hibp_failure(cur)
        return "hibp_unavailable"

def _alert_admin_hibp_failure():
    """
    Alert admin if repeated HIBP failures occur. This is a placeholder for real alerting (email, etc).
    """
    # Use DB-persisted failure count if available
    # Accepts optional cur for DB context, avoids circular import
    from .alerting import alert_security_event
    count = 0
    if hasattr(_alert_admin_hibp_failure, "cur") and _alert_admin_hibp_failure.cur is not None:
        cur = _alert_admin_hibp_failure.cur
        count = _get_hibp_failure_count(cur)
        if count >= _HIBP_FAILURE_THRESHOLD:
            alert_security_event(
                "hibp_repeated_failure",
                {
                    "failure_count": count,
                    "message": "Repeated HIBP API failures detected. Password breach checks are not functioning."
                },
            )
            _reset_hibp_failure_count(cur)
    elif hasattr(_alert_admin_hibp_failure, "cur"):
        pass
    elif hasattr(_alert_admin_hibp_failure, "conn") and _alert_admin_hibp_failure.conn is not None:
        cur = _alert_admin_hibp_failure.conn.cursor()
        count = _get_hibp_failure_count(cur)
        if count >= _HIBP_FAILURE_THRESHOLD:
            alert_security_event(
                "hibp_repeated_failure",
                {
                    "failure_count": count,
                    "message": "Repeated HIBP API failures detected. Password breach checks are not functioning."
                },
            )
            _reset_hibp_failure_count(cur)
    else:
        # No cursor provided, try to get one if possible
        try:
            from ..db.connection import get_db
            conn = get_db()
            cur = conn.cursor()
            count = _get_hibp_failure_count(cur)
            if count >= _HIBP_FAILURE_THRESHOLD:
                alert_security_event(
                    "hibp_repeated_failure",
                    {
                        "failure_count": count,
                        "message": "Repeated HIBP API failures detected. Password breach checks are not functioning."
                    },
                )
                _reset_hibp_failure_count(cur)
        except Exception:
            pass
def ensure_admin_user(
    conn: sqlite3.Connection,
    cur: sqlite3.Cursor,
    username: str = None,
    password: str = None
) -> None:
    """
    Bootstrap: create the first admin if there are no users.
    username/password: If provided, used directly. If not, caller must prompt.
    """
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bootstrap_lock (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                done INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()

        cur.execute("BEGIN IMMEDIATE")
        cur.execute("INSERT OR IGNORE INTO bootstrap_lock (id, done) VALUES (1, 0)")
        cur.execute("SELECT done FROM bootstrap_lock WHERE id = 1")
        done = int((cur.fetchone() or [0])[0] or 0)
        if done:
            conn.rollback()
            return

        cur.execute("SELECT COUNT(*) FROM users")
        count = int((cur.fetchone() or [0])[0] or 0)
        if count > 0:
            conn.rollback()
            return

        if username is None or password is None:
            raise ValueError("Username and password must be provided for non-interactive bootstrap.")
        try:
            create_user(conn=conn, cur=cur, username=username, password=password, role="admin")
        except Exception as e:
            conn.rollback()
            raise
        audit("user.create", entity_type="user", details={"username": username, "role": "admin", "bootstrap": True})

        # Mark bootstrap as done
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("UPDATE bootstrap_lock SET done = 1 WHERE id = 1")
        conn.commit()
        return
    except Exception as e:
        # Failsafe unlock on error
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("UPDATE bootstrap_lock SET done = 1 WHERE id = 1")
            conn.commit()
        except Exception:
            pass
        conn.rollback()
        raise


# --- CLI Wrappers (for interactive use only) ---
@require_role("admin")
def admin_create_user(prompt_fn=None, getpass_fn=None) -> None:
    """
    Interactive CLI wrapper for user creation. Not for production automation.
    prompt_fn: function to prompt for username/role (default: prompt_non_empty)
    getpass_fn: function to prompt for password (default: getpass.getpass)
    """
    if prompt_fn is None:
        from ..prompts import prompt_non_empty as prompt_fn
    if getpass_fn is None:
        import getpass
        getpass_fn = getpass.getpass
    username = prompt_fn("New username: ")
    role = prompt_fn("Role (admin/fundraiser/viewer): ").strip().lower()
    if role not in CANONICAL_ROLES:
        print(f"Invalid role. Must be one of: {', '.join(CANONICAL_ROLES)}")
        audit("user.create.failed", entity_type="user", details={
            "username": username,
            "reason": "invalid_role_input",
            "role": role
        })
        return
    while True:
        pw1 = getpass_fn("Password (min 12 chars): ")
        pw2 = getpass_fn("Confirm password: ")
        if pw1 != pw2:
            print("Passwords do not match.")
            continue
        try:
            _enforce_password_policy(username, pw1, role)
            break
        except ValueError as e:
            print(e)
            continue

    def op(conn: Any, cur: Any) -> None:
        create_user(conn=conn, cur=cur, username=username, password=pw1, role=role)

    try:
        run_db(op, write=True)
    except ValueError as e:
        print(e)
        return
    except sqlite3.Error as e:
        print(f"Could not create user: {e}")
        return

    print("User created.")
    audit("user.create", entity_type="user", details={"username": username, "role": role, "created_by": CURRENT_USER.get("username") if CURRENT_USER else None})
