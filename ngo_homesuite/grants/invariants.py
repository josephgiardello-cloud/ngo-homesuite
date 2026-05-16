"""Cross-service grant invariants."""

from __future__ import annotations


def require_same_org(expected_org_id: int, actual_org_id: int, *, entity: str) -> None:
    if int(expected_org_id) != int(actual_org_id):
        raise ValueError(f"cross-tenant {entity} access is not allowed")
