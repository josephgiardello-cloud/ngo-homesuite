from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Tuple, TypeVar

from ..config import DB_ENCRYPTION_KEY_ENV, DB_PATH


T = TypeVar("T")


def _escape_sqlite_single_quotes(value: str) -> str:
    return value.replace("'", "''")


def _looks_like_hex(value: str) -> bool:
    v = value.strip()
    if len(v) < 32 or (len(v) % 2) != 0:
        return False
    return re.fullmatch(r"[0-9a-fA-F]+", v) is not None


def _sqlcipher_apply_key(conn: Any, key: str) -> None:
    """Apply SQLCipher key.

    Supports:
    - passphrase in NGOHS_DB_KEY
    - hex key material when prefixed with 'hex:' (recommended)
      Example: NGOHS_DB_KEY=hex:001122... (even-length hex)
    """

    key = key.strip()
    if key.lower().startswith("hex:"):
        hex_key = key[4:].strip()
        if not _looks_like_hex(hex_key):
            raise ValueError("Invalid hex key format")
        conn.execute(f"PRAGMA key = \"x'{hex_key.lower()}'\"")
        return

    try:
        conn.execute("PRAGMA key = ?", (key,))
        return
    except (TypeError, sqlite3.Error):
        pass

    safe_key = _escape_sqlite_single_quotes(key)
    conn.execute(f"PRAGMA key = '{safe_key}'")


def connect_db_at(path: str):
    """Connect to DB; uses SQLCipher if installed and NGOHS_DB_KEY is set."""

    key = os.environ.get(DB_ENCRYPTION_KEY_ENV)
    if key:
        try:
            from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore

            conn = sqlcipher.connect(path)
            _sqlcipher_apply_key(conn, key)
            return conn
        except ModuleNotFoundError:
            raise SystemExit(
                f"ERROR: {DB_ENCRYPTION_KEY_ENV} is set but SQLCipher driver isn't installed. "
                "Refusing to open an unencrypted database. Install SQLCipher support (e.g., pysqlcipher3) "
                "or unset the environment variable."
            )
        except (ValueError, sqlite3.Error, OSError) as e:
            raise SystemExit(f"ERROR: Failed to open encrypted DB: {e}")

    return sqlite3.connect(path)


# Back-compat aliases (match legacy NGOMG.py helper names)
def _connect_db_at(path: str):
    return connect_db_at(path)


def connect_db():
    return connect_db_at(DB_PATH)


def _connect_db():
    return connect_db()


def configure_connection(conn: sqlite3.Connection) -> None:
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


def _configure_connection(conn: sqlite3.Connection) -> None:
    configure_connection(conn)


@contextmanager
def open_db() -> Iterator[Tuple[Any, Any]]:
    """Open a DB connection and cursor (fresh per call)."""

    conn = connect_db()
    configure_connection(conn)
    cur = conn.cursor()
    try:
        yield conn, cur
    finally:
        try:
            cur.close()
        except sqlite3.Error:
            pass
        try:
            conn.close()
        except sqlite3.Error:
            pass


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
            if _is_transient_db_error(exc) and attempt < retries - 1:
                last_exc = exc
                time.sleep(base_delay_s * (2**attempt))
                continue
            raise

    raise SystemExit(f"ERROR: Database unavailable: {last_exc}")
