"""Application-level tenant isolation helpers.

This module applies organization scoping to ORM SELECT statements by using
SQLAlchemy session events. It is not a database-native RLS replacement, but it
adds a defensive layer for accidental unscoped ORM queries.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import contextmanager
from contextvars import ContextVar

from flask import current_app, g, has_app_context, has_request_context, request
from flask_login import current_user
from sqlalchemy import and_, event, false, inspect, literal_column, select, table
from sqlalchemy.orm import Session, registry, with_loader_criteria
from sqlalchemy.sql.elements import (
    ReleaseSavepointClause,
    RollbackToSavepointClause,
    SavepointClause,
)

from ngo_homesuite.models.core import (
    AIConversation,
    AIMessage,
    Donation,
    DonationReceipt,
    P2PPage,
    P2PPageDonation,
    StewardshipJourney,
    StewardshipStep,
    User,
    db,
)

# Login/user loading happens before request membership resolution. User reads
# require explicit authorization at their callsites, not this tenant listener.
TENANT_READ_EXCLUSIONS = {
    User: "Authentication/bootstrap identity lookup precedes tenant resolution."
}
_BACKGROUND_ORG: ContextVar[int | None] = ContextVar("tenant_background_org", default=None)
_VALIDATED_FLUSH: ContextVar[bool] = ContextVar("tenant_validated_flush", default=False)
_SCOPED_EXECUTION = object()

INDIRECT_TENANT_OWNERS = {
    AIMessage: (("conversation_id", AIConversation),),
    DonationReceipt: (("donation_id", Donation),),
    StewardshipStep: (("journey_id", StewardshipJourney),),
    P2PPageDonation: (("page_id", P2PPage), ("donation_id", Donation)),
}


def require_tenant_context() -> int:
    """Require an explicit, valid tenant before worker/service execution."""
    organization_id = _resolve_org_id()
    if organization_id is None or organization_id <= 0:
        raise PermissionError("An explicit positive tenant context is required")
    return organization_id


def _strict_sql_boundary() -> bool:
    return _BACKGROUND_ORG.get() is not None or (
        has_request_context() and bool(current_app.config.get("TENANT_SQL_GUARD_ENABLED", False))
    )


def _indirect_criterion(model, organization_id):
    return and_(*(
        select(parent.id).where(
            parent.id == getattr(model, key),
            parent.organization_id == organization_id,
        ).correlate(model).exists()
        for key, parent in INDIRECT_TENANT_OWNERS[model]
    ))


def tenant_scoped_models() -> tuple[tuple[type, str], ...]:
    """Discover registered direct/inherited tenant columns, including late imports."""
    models = []
    model_registry = getattr(db.Model, "registry", None)
    if not isinstance(model_registry, registry):
        raise TypeError("Tenant isolation requires a SQLAlchemy model registry")
    for mapper in model_registry.mappers:
        model = mapper.class_
        if model in TENANT_READ_EXCLUSIONS:
            continue
        for key in ("organization_id", "org_id"):
            if key in mapper.column_attrs:
                models.append((model, key))
                break
    return tuple(sorted(models, key=lambda item: (item[0].__module__, item[0].__name__)))


_LISTENERS_REGISTERED = False


def _resolve_org_id() -> int | None:
    """Resolve tenant org id from request context.

    ``g.organization_id`` is set from the authenticated user by request
    middleware, or explicitly by trusted background jobs/tests. Missing
    membership in an authenticated request must not become system access.
    """
    background_org = _BACKGROUND_ORG.get()
    if background_org is not None:
        return background_org
    if not has_app_context():
        return None

    g_org = getattr(g, "organization_id", None)
    if g_org is not None:
        if isinstance(g_org, bool) or not isinstance(g_org, int) or g_org <= 0:
            raise PermissionError("Invalid tenant context")
        return g_org

    return None


def _iter_tenant_objects(session: Session) -> Iterable[object]:
    for collection in (session.new, session.dirty, session.deleted):
        for obj in collection:
            if hasattr(obj, "organization_id") or hasattr(obj, "org_id") or type(obj) in INDIRECT_TENANT_OWNERS:
                yield obj


@contextmanager
def background_tenant_context(
    organization_id: int,
    *,
    actor: str,
    reason: str,
    audit: Callable[[dict], None],
):
    """Opt-in worker boundary; callers must supply a durable audit sink.

    Commits remain the caller's responsibility. Clear the identity map at both
    boundaries because Session.get() can otherwise bypass SELECT listeners.
    This does not make raw SQL, Core DML, or existing unwired workers tenant-safe.
    """
    if not has_app_context() or has_request_context():
        raise RuntimeError("Background tenant context requires a non-request app context")
    if _BACKGROUND_ORG.get() is not None:
        raise RuntimeError("Nested background tenant contexts are not supported")
    if isinstance(organization_id, bool) or not isinstance(organization_id, int) or organization_id <= 0:
        raise ValueError("A positive integer organization_id is required")
    if not actor.strip() or not reason.strip() or not callable(audit):
        raise ValueError("An actor, reason, and audit sink are required")
    if db.session.new or db.session.dirty or db.session.deleted:
        raise RuntimeError("Background tenant context requires a clean session")
    db.session.remove()
    from ngo_homesuite.models.core import Organization

    if db.session.get(Organization, organization_id) is None:
        db.session.remove()
        raise PermissionError("Unknown tenant organization")
    db.session.remove()
    payload = {"organization_id": organization_id, "actor": actor, "reason": reason}
    audit({**payload, "event": "tenant_context_started"})
    token = _BACKGROUND_ORG.set(organization_id)
    outcome = "failed"
    try:
        yield
        outcome = "completed"
    finally:
        try:
            db.session.remove()
        finally:
            _BACKGROUND_ORG.reset(token)
            audit({**payload, "event": "tenant_context_finished", "outcome": outcome})


def _missing_tenant_membership() -> bool:
    return (
        has_app_context()
        and bool(getattr(g, "tenant_membership_required", False))
        and _resolve_org_id() is None
    )


def register_rls_listeners(app) -> None:
    """Register SQLAlchemy listeners for tenant read/write isolation once."""
    global _LISTENERS_REGISTERED
    app.extensions["tenant_rls_models"] = tuple(model.__name__ for model, _ in tenant_scoped_models())
    app.extensions["tenant_rls_read_exclusions"] = {
        model.__name__: reason for model, reason in TENANT_READ_EXCLUSIONS.items()
    }
    with app.app_context():
        for engine in db.engines.values():
            if (
                engine.dialect.name == "postgresql"
                and app.config.get("FLASK_ENV") == "production"
                and app.config.get("APP_PROCESS_ROLE") != "migrator"
            ):
                with engine.connect() as connection:
                    superuser, bypass = connection.execute(
                        select(
                            literal_column("rolsuper"),
                            literal_column("rolbypassrls"),
                        ).select_from(table("pg_roles")).where(
                            literal_column("rolname") == literal_column("current_user")
                        )
                    ).one()
                    if superuser or bypass:
                        raise RuntimeError("Production PostgreSQL application role must be NOSUPERUSER NOBYPASSRLS")
            if not event.contains(engine, "before_cursor_execute", _guard_sql_execution):
                event.listen(engine, "before_cursor_execute", _guard_sql_execution)
    if _LISTENERS_REGISTERED:
        return

    @event.listens_for(Session, "do_orm_execute")
    def _enforce_tenant_select_scope(execute_state):
        if _strict_sql_boundary():
            require_tenant_context()
            if not execute_state.is_orm_statement or not execute_state.is_select:
                raise PermissionError("Raw SQL and bulk DML are forbidden in a strict tenant boundary")
            if execute_state.execution_options.get("skip_tenant_rls"):
                raise PermissionError("Tenant scope bypass is forbidden in a strict tenant boundary")
            execute_state.update_execution_options(_tenant_scoped_execution=_SCOPED_EXECUTION)
        if not execute_state.is_select:
            return
        if execute_state.execution_options.get("skip_tenant_rls"):
            return

        org_id = _resolve_org_id()
        if org_id is None:
            if _missing_tenant_membership():
                stmt = execute_state.statement
                for model, _ in tenant_scoped_models():
                    stmt = stmt.options(with_loader_criteria(model, false(), include_aliases=True))
                for model in INDIRECT_TENANT_OWNERS:
                    stmt = stmt.options(with_loader_criteria(model, false(), include_aliases=True))
                execute_state.statement = stmt
            return

        stmt = execute_state.statement
        for model, key in tenant_scoped_models():
            stmt = stmt.options(
                with_loader_criteria(
                    model,
                    getattr(model, key) == (str(org_id) if key == "org_id" else org_id),
                    include_aliases=True,
                )
            )
        for model in INDIRECT_TENANT_OWNERS:
            stmt = stmt.options(
                with_loader_criteria(model, _indirect_criterion(model, org_id), include_aliases=True)
            )
        execute_state.statement = stmt

    @event.listens_for(Session, "before_flush")
    def _enforce_tenant_write_scope(session, flush_context, instances):
        del flush_context, instances
        org_id = _resolve_org_id()
        if _strict_sql_boundary():
            require_tenant_context()
        if org_id is None:
            if _missing_tenant_membership():
                for obj in _iter_tenant_objects(session):
                    if (
                        isinstance(obj, User) and obj.organization_id is None
                        and has_request_context() and request.blueprint == "auth"
                        and current_user.is_authenticated and obj.id == current_user.id
                        and obj not in session.new and obj not in session.deleted
                    ):
                        changed = {
                            attribute.key for attribute in inspect(obj).attrs
                            if attribute.history.has_changes()
                        }
                        if changed <= {
                            "last_login", "mfa_enabled", "mfa_totp_secret", "mfa_backup_codes_json",
                            "mfa_failed_attempts", "mfa_attempt_window_started_at", "mfa_locked_until",
                            "webauthn_credentials_json", "failed_login_count", "locked_until",
                        }:
                            continue
                    raise PermissionError("Authenticated user has no tenant organization assignment")
            return

        for obj in _iter_tenant_objects(session):
            if type(obj) in INDIRECT_TENANT_OWNERS:
                for key, parent in INDIRECT_TENANT_OWNERS[type(obj)]:
                    parent_id = getattr(obj, key)
                    relationship_values = [
                        value
                        for relationship in inspect(obj).mapper.relationships
                        if getattr(type(obj), key).property.columns[0] in relationship.local_columns
                        for value in inspect(obj).attrs[relationship.key].history.added
                        if isinstance(value, parent)
                    ]
                    related = [
                        value for value in session.new
                        if isinstance(value, parent) and value.id == parent_id and parent_id is not None
                    ]
                    if relationship_values:
                        parent_org = relationship_values[0].organization_id
                    elif related:
                        parent_org = related[0].organization_id
                    else:
                        parent_org = session.connection().execute(
                            select(parent.__table__.c.organization_id).where(parent.__table__.c.id == parent_id)
                            .execution_options(_tenant_scoped_execution=_SCOPED_EXECUTION)
                        ).scalar_one_or_none()
                    if parent_org != org_id:
                        raise PermissionError("Tenant isolation violation: indirect parent is missing or belongs to another tenant")
                continue
            obj_org = getattr(obj, "organization_id", getattr(obj, "org_id", None))
            if obj_org is None:
                if _strict_sql_boundary():
                    raise PermissionError("Tenant-owned writes require an organization")
                continue
            if int(obj_org) != int(org_id):
                raise PermissionError(
                    f"Tenant isolation violation: {obj.__class__.__name__} organization_id={obj_org} does not match active org_id={org_id}."
                )
            if isinstance(obj, P2PPage):
                for donation in inspect(obj).attrs.donations.history.added:
                    if donation.organization_id != org_id:
                        raise PermissionError("Tenant isolation violation: indirect association parent belongs to another tenant")
        if _strict_sql_boundary():
            _VALIDATED_FLUSH.set(True)

    @event.listens_for(Session, "after_flush_postexec")
    @event.listens_for(Session, "after_soft_rollback")
    def _clear_validated_flush(session, *args):
        _VALIDATED_FLUSH.set(False)

    _LISTENERS_REGISTERED = True


def _guard_sql_execution(conn, cursor, statement, parameters, context, executemany):
    if context.compiled is not None and isinstance(
        context.compiled.statement, (SavepointClause, ReleaseSavepointClause, RollbackToSavepointClause),
    ):
        return
    if conn.dialect.name == "postgresql":
        organization_id = _resolve_org_id()
        cursor.execute(
            "SELECT set_config('ngo.organization_id', %s, true)",
            (str(organization_id) if organization_id is not None else "",),
        )
    if not _strict_sql_boundary():
        return
    require_tenant_context()
    if context.execution_options.get("_tenant_scoped_execution") is _SCOPED_EXECUTION:
        return
    if _VALIDATED_FLUSH.get() and context.compiled is not None and (
        context.isinsert or context.isupdate or context.isdelete
    ):
        return
    raise PermissionError("Unscoped engine SQL is forbidden in a strict tenant boundary")
