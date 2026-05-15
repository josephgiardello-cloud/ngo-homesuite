from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    org_id: str
    user_id: str
    role: str


def assert_tenant_match(expected_org_id: str, actual_org_id: str) -> None:
    if expected_org_id != actual_org_id:
        raise PermissionError(f"Tenant isolation violation: expected {expected_org_id}, got {actual_org_id}")
