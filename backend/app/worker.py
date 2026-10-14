from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from ngo_homesuite.domain.outbox import deliver_pending

from .config import load_store


class SQLiteEventSink:
    """Durable local integration inbox; stable IDs make redelivery harmless."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS received (
                event_key TEXT PRIMARY KEY, payload TEXT NOT NULL)""")

    def __call__(self, key: str, payload: str) -> None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT payload FROM received WHERE event_key = ?", (key,)).fetchone()
            if row is not None and row[0] != payload:
                raise ValueError("Event ID already exists with different content")
            conn.execute("INSERT OR IGNORE INTO received VALUES (?, ?)", (key, payload))


def main() -> None:
    parser = argparse.ArgumentParser(description="Drain a tenant's durable events to an idempotent local inbox")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("tenant")
    parser.add_argument("sink", type=Path)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("limit must be positive")
    store = load_store(args.manifest)
    # The sink is a separate integration database, not the business database.
    if store.is_tenant_database(args.sink):
        parser.error("sink must not be a tenant database")
    count = deliver_pending(store, args.tenant, SQLiteEventSink(args.sink), limit=args.limit)
    print(f"Delivered {count} events")


if __name__ == "__main__":
    main()
