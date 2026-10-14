"""Tenant-bound scheduled execution; deployment configuration authorizes the scope."""
from __future__ import annotations

from collections.abc import Callable

from flask import Flask

from ngo_homesuite.audit.security_events import SecurityAuditService, SecurityEventType
from ngo_homesuite.db.rls import background_tenant_context


def run_tenant_job(app: Flask, *, name: str, callback: Callable[[], None]) -> None:
    organizations = app.config.get("TENANT_WORKER_ORGANIZATION_IDS")
    if (
        not isinstance(organizations, (list, tuple)) or not organizations
        or any(type(value) is not int or value <= 0 for value in organizations)
        or len(set(organizations)) != len(organizations)
    ):
        raise PermissionError("Scheduled jobs require an explicit tenant organization allowlist")
    if not name.strip():
        raise ValueError("Scheduled jobs require a name")
    with app.app_context():
        for organization_id in organizations:
            def audit(payload, *, tenant_id=organization_id):
                SecurityAuditService.log_event(
                    SecurityEventType.BACKGROUND_TENANT_CONTEXT,
                    payload["event"],
                    resource_type="background_job",
                    resource_org_id=tenant_id,
                    result="failure" if payload.get("outcome") == "failed" else "success",
                    payload=payload,
                )

            with background_tenant_context(
                organization_id, actor=f"scheduler:{name}", reason=name, audit=audit,
            ):
                callback()
