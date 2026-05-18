from __future__ import annotations

import os
import time
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

_engine = None
_Session = None

def _default_sqlite_url() -> str:
    db_path = os.environ.get("NGO_HOMESUITE_DB_PATH", "data/ngo_homesuite.db")
    normalized = Path(db_path).expanduser()
    return f"sqlite:///{normalized.as_posix()}"


def _engine_url(db_path: str | None) -> str:
    if db_path and "://" in db_path:
        return db_path
    if db_path:
        normalized = Path(db_path).expanduser()
        return f"sqlite:///{normalized.as_posix()}"
    return os.environ.get("DATABASE_URL") or _default_sqlite_url()


def init_engine(db_path: str | None = None):
    global _engine, _Session
    if _engine:
        return
    url = _engine_url(db_path)
    connect_args = {"timeout": 30} if url.startswith("sqlite:///") else {}
    _engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    if url.startswith("sqlite:///"):
        @event.listens_for(_engine, "connect")
        def _sqlite_tuning(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)

def get_session():
    if not _Session:
        raise RuntimeError("Database engine not initialized")
    return _Session()


def run_with_retry(work, *, retries: int = 3, base_delay_seconds: float = 0.2):
    last_error: OperationalError | None = None
    for attempt in range(retries):
        try:
            return work()
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == retries - 1:
                raise
            last_error = exc
            time.sleep(base_delay_seconds * (2 ** attempt))
    if last_error is not None:
        raise last_error
