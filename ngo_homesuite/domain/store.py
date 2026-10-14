from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ..db.connection import configure_connection, connect_db_at
from ..db.schema import SCHEMA_VERSION


class DomainError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


class TenantStore:
    """A trusted configuration selects a whole database, never a SQL tenant filter."""

    def __init__(self, databases: Mapping[str, str | Path]) -> None:
        if not databases:
            raise ValueError("At least one tenant database is required")
        self._databases = {tenant: Path(path).resolve() for tenant, path in databases.items()}
        if len(set(self._databases.values())) != len(self._databases):
            raise ValueError("Organizations must not share a database")
        existing = [path for path in self._databases.values() if path.exists()]
        for index, path in enumerate(existing):
            if any(path.samefile(other) for other in existing[index + 1:]):
                raise ValueError("Organizations must not share a database")

    @contextmanager
    def transaction(self, tenant: str, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        path = self._databases.get(tenant)
        if path is None:
            raise DomainError("Unknown organization", 404)
        if not path.is_file():
            raise DomainError("Organization database is not provisioned", 503)
        conn = connect_db_at(str(path))
        try:
            configure_connection(conn)
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
            if row is None or row[0] != str(SCHEMA_VERSION):
                raise DomainError("Database migration required", 503)
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def is_tenant_database(self, path: Path) -> bool:
        resolved = path.resolve()
        return any(
            resolved == database or (resolved.exists() and database.exists() and resolved.samefile(database))
            for database in self._databases.values()
        )
