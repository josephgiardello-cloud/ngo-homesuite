"""Application-level tenant isolation helpers.

This module applies organization scoping to ORM SELECT statements by using
SQLAlchemy session events. It is not a database-native RLS replacement, but it
adds a defensive layer for accidental unscoped ORM queries.
"""

from __future__ import annotations

from typing import Iterable, Optional

from flask import g, has_app_context
from sqlalchemy import event, false
from sqlalchemy.orm import Session, with_loader_criteria

from ngo_homesuite.models.core import (
    Campaign,
    Donation,
    Donor,
    Expense,
    Fund,
    Grant,
    GrantBudgetLine,
    GrantExpenseAllocation,
    ProgramCase,
)

_ORG_SCOPED_MODELS: tuple[type, ...] = (
    Campaign,
    Donation,
    Donor,
    Expense,
    Fund,
    Grant,
    GrantBudgetLine,
    GrantExpenseAllocation,
    ProgramCase,
)

_LISTENERS_REGISTERED = False


def _resolve_org_id() -> Optional[int]:
    """Resolve tenant org id from request context.

    ``g.organization_id`` is set from the authenticated user by request
    middleware, or explicitly by trusted background jobs/tests. Missing
    membership in an authenticated request must not become system access.
    """
    if not has_app_context():
        return None

    g_org = getattr(g, "organization_id", None)
    if g_org is not None:
        try:
            return int(g_org)
        except (TypeError, ValueError):
            return None

    return None


def _iter_tenant_objects(session: Session) -> Iterable[object]:
    for collection in (session.new, session.dirty, session.deleted):
        for obj in collection:
            if hasattr(obj, "organization_id"):
                yield obj


def _missing_tenant_membership() -> bool:
    return (
        has_app_context()
        and bool(getattr(g, "tenant_membership_required", False))
        and _resolve_org_id() is None
    )


def register_rls_listeners(app) -> None:
    """Register SQLAlchemy listeners for tenant read/write isolation once."""
    global _LISTENERS_REGISTERED
    if _LISTENERS_REGISTERED:
        return

    @event.listens_for(Session, "do_orm_execute")
    def _enforce_tenant_select_scope(execute_state):
        if not execute_state.is_select:
            return
        if execute_state.execution_options.get("skip_tenant_rls"):
            return

        org_id = _resolve_org_id()
        if org_id is None:
            if _missing_tenant_membership():
                stmt = execute_state.statement
                for model in _ORG_SCOPED_MODELS:
                    stmt = stmt.options(with_loader_criteria(model, false(), include_aliases=True))
                execute_state.statement = stmt
            return

        stmt = execute_state.statement
        for model in _ORG_SCOPED_MODELS:
            stmt = stmt.options(
                with_loader_criteria(
                    model,
                    lambda cls: cls.organization_id == org_id,
                    include_aliases=True,
                )
            )
        execute_state.statement = stmt

    @event.listens_for(Session, "before_flush")
    def _enforce_tenant_write_scope(session, flush_context, instances):
        del flush_context, instances
        org_id = _resolve_org_id()
        if org_id is None:
            if _missing_tenant_membership():
                for obj in _iter_tenant_objects(session):
                    if getattr(obj, "organization_id", None) is not None:
                        raise PermissionError("Authenticated user has no tenant organization assignment")
            return

        for obj in _iter_tenant_objects(session):
            obj_org = getattr(obj, "organization_id", None)
            if obj_org is None:
                continue
            if int(obj_org) != int(org_id):
                raise PermissionError(
                    f"Tenant isolation violation: {obj.__class__.__name__} organization_id={obj_org} does not match active org_id={org_id}."
                )

    app.extensions["tenant_rls_models"] = tuple(model.__name__ for model in _ORG_SCOPED_MODELS)
    _LISTENERS_REGISTERED = True
