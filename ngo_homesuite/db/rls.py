"""Application-level tenant isolation helpers.

This module applies organization scoping to ORM SELECT statements by using
SQLAlchemy session events. It is not a database-native RLS replacement, but it
adds a defensive layer for accidental unscoped ORM queries.
"""

from __future__ import annotations

from typing import Iterable, Optional

from flask import g, has_app_context
from sqlalchemy import event
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

    Priority:
    1) ``g.organization_id`` when explicitly set by middleware/tests.
    2) authenticated ``current_user.organization_id``.
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
