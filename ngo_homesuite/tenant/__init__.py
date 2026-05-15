"""Tenant isolation guards and helpers."""

from .context import TenantContext, assert_tenant_match

__all__ = ["TenantContext", "assert_tenant_match"]
