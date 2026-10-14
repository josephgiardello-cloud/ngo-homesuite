from __future__ import annotations

import logging
from collections.abc import Callable

from ..prompts import utc_now_iso
from .store import TenantStore

log = logging.getLogger(__name__)
Delivery = Callable[[str, str], None]


def deliver_pending(store: TenantStore, tenant: str, deliver: Delivery, *, limit: int = 100) -> int:
    """At-least-once delivery; consumers must deduplicate the stable tenant:event ID."""
    with store.transaction(tenant) as conn:
        pending = conn.execute(
            "SELECT id, payload FROM outbox WHERE delivered_at IS NULL ORDER BY id LIMIT ?", (limit,),
        ).fetchall()
    count = 0
    for row in pending:
        event_id = row["id"]
        # Persist the attempt before calling the external sink, including process-crash attempts.
        with store.transaction(tenant, write=True) as conn:
            conn.execute("UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (event_id,))
        try:
            deliver(f"{tenant}:{event_id}", row["payload"])
        except Exception as exc:
            log.exception("Outbox delivery failed: tenant=%s event=%s", tenant, event_id)
            with store.transaction(tenant, write=True) as conn:
                conn.execute("UPDATE outbox SET last_error = ? WHERE id = ?", (str(exc)[:1000], event_id))
            raise
        with store.transaction(tenant, write=True) as conn:
            conn.execute("UPDATE outbox SET delivered_at = ?, last_error = NULL WHERE id = ?",
                         (utc_now_iso(), event_id))
        count += 1
    return count
