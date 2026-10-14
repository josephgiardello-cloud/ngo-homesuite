from __future__ import annotations

from uuid import uuid4

import pytest
from flask import g
from sqlalchemy import event, func, literal, select, text, union_all
from sqlalchemy.orm import aliased

from ngo_homesuite.db.rls import (
    TENANT_READ_EXCLUSIONS,
    background_tenant_context,
    tenant_scoped_models,
)
from ngo_homesuite.models.core import Beneficiary, Organization, User, Volunteer, db
from ngo_homesuite.persistence.models.workflow_tables import WorkflowInstanceRecord


@pytest.fixture()
def tenant_context(shared_test_app):
    with shared_test_app.app_context():
        suffix = uuid4().hex
        orgs = [
            Organization(name=f"Registry {label}", slug=f"registry-{label}-{suffix}")
            for label in ("a", "b")
        ]
        db.session.add_all(orgs)
        db.session.commit()
        yield orgs
        db.session.rollback()


def test_registry_covers_every_registered_tenant_column():
    expected = {
        mapper.class_
        for mapper in db.Model.registry.mappers
        if any(key in mapper.column_attrs for key in ("organization_id", "org_id"))
        and mapper.class_ not in TENANT_READ_EXCLUSIONS
    }
    assert {model for model, _ in tenant_scoped_models()} == expected
    assert User in TENANT_READ_EXCLUSIONS
    assert len(expected) > 9


def test_every_registered_model_emits_bound_tenant_predicate(tenant_context):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        for org in tenant_context:
            g.organization_id = org.id
            for model, key in tenant_scoped_models():
                statements.clear()
                list(db.session.scalars(select(model).limit(0)))
                assert statements, model.__name__
                sql, params = statements[0]
                assert key in sql.partition("WHERE")[2], (model.__name__, sql)
                assert org.id in params or str(org.id) in params, (model.__name__, params)
        g.organization_id = None
        g.tenant_membership_required = True
        for model, _ in tenant_scoped_models():
            statements.clear()
            assert list(db.session.scalars(select(model).limit(0))) == []
            assert "0 = 1" in statements[0][0], model.__name__
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)


def test_every_registered_model_filters_two_tenant_aliased_rows(tenant_context):
    org_ids = [org.id for org in tenant_context]
    for model, key in tenant_scoped_models():
        values = [str(org_id) if key == "org_id" else org_id for org_id in org_ids]
        synthetic = union_all(*(
            select(literal(value).label(key)) for value in values
        )).subquery()
        tenant_alias = aliased(model, synthetic, adapt_on_names=True)
        statement = select(getattr(tenant_alias, key))
        for org_id, value in zip(org_ids, values):
            g.organization_id = org_id
            assert list(db.session.scalars(statement)) == [value], model.__name__
        assert list(db.session.scalars(statement.execution_options(skip_tenant_rls=True))) == values
        g.organization_id = None
        g.tenant_membership_required = True
        assert list(db.session.scalars(statement)) == [], model.__name__
        g.tenant_membership_required = False


def test_every_registered_model_rejects_cross_tenant_flush(tenant_context):
    org_ids = [org.id for org in tenant_context]
    for active_org, other_org in (org_ids, org_ids[::-1]):
        g.organization_id = active_org
        for model, key in tenant_scoped_models():
            db.session.add(model(**{key: str(other_org) if key == "org_id" else other_org}))
            with pytest.raises(PermissionError, match="Tenant isolation violation"):
                db.session.flush()
            db.session.rollback()


@pytest.mark.parametrize("model", [Beneficiary, Volunteer])
def test_newly_scoped_models_two_tenant_alias_count_and_id(tenant_context, model):
    org_ids = [org.id for org in tenant_context]
    rows = [
        model(organization_id=org_id, **(
            {"first_name": "Private", "last_name": str(org_id)}
            if model is Beneficiary else {"name": f"Private {org_id}"}
        ))
        for org_id in org_ids
    ]
    db.session.add_all(rows)
    db.session.commit()
    ids = [row.id for row in rows]
    db.session.expunge_all()
    alias = aliased(model)
    for own_org, own_id, other_id in ((org_ids[0], ids[0], ids[1]), (org_ids[1], ids[1], ids[0])):
        g.organization_id = own_org
        assert [row.id for row in db.session.scalars(select(alias))] == [own_id]
        assert db.session.scalar(select(func.count()).select_from(model)) == 1
        assert db.session.get(model, other_id) is None
        db.session.expunge_all()
    g.organization_id = None
    g.tenant_membership_required = True
    assert list(db.session.scalars(select(model))) == []


def test_workflow_org_id_reads_and_flush_writes_are_scoped(tenant_context):
    org_ids = [org.id for org in tenant_context]
    ids = [f"registry-{uuid4().hex}" for _ in tenant_context]
    rows = [
        WorkflowInstanceRecord(
            instance_id=instance_id, org_id=str(org.id), workflow_type="inventory",
            current_step="new", status="active",
        )
        for org, instance_id in zip(tenant_context, ids)
    ]
    db.session.add_all(rows)
    db.session.commit()
    db.session.expunge_all()
    g.organization_id = org_ids[0]
    assert [row.instance_id for row in db.session.scalars(select(WorkflowInstanceRecord))] == [ids[0]]
    db.session.add(WorkflowInstanceRecord(
        instance_id=f"registry-{uuid4().hex}", org_id=str(org_ids[1]),
        workflow_type="inventory", current_step="new", status="active",
    ))
    with pytest.raises(PermissionError, match="Tenant isolation violation"):
        db.session.flush()
    db.session.rollback()


def test_background_context_audits_restores_and_clears_cached_rows(tenant_context):
    events = []
    org_a, org_b = tenant_context
    row = Volunteer(organization_id=org_b.id, name="Worker private")
    db.session.add(row)
    db.session.commit()
    row_id = row.id
    assert db.session.get(Volunteer, row_id) is row
    g.organization_id = org_b.id
    with background_tenant_context(org_a.id, actor="test-worker", reason="delivery", audit=events.append):
        assert db.session.get(Volunteer, row_id) is None
        assert g.organization_id == org_b.id
        with pytest.raises(RuntimeError, match="Nested"), background_tenant_context(
            org_b.id, actor="test-worker", reason="nested", audit=events.append,
        ):
            pass
    assert db.session.get(Volunteer, row_id).organization_id == org_b.id
    assert [entry["event"] for entry in events] == ["tenant_context_started", "tenant_context_finished"]
    assert events[-1]["outcome"] == "completed"


def test_background_context_restores_after_failure_and_audit_failure(tenant_context):
    events = []
    org_a, org_b = tenant_context
    g.organization_id = org_b.id
    with pytest.raises(ValueError, match="worker failure"), background_tenant_context(
        org_a.id, actor="worker", reason="test", audit=events.append,
    ):
        raise ValueError("worker failure")
    assert events[-1]["outcome"] == "failed"

    def fail_audit(payload):
        raise RuntimeError("audit unavailable")

    with pytest.raises(RuntimeError, match="audit unavailable"), background_tenant_context(
        org_a.id, actor="worker", reason="test", audit=fail_audit,
    ):
        pytest.fail("Unaudited work must not start")
    from ngo_homesuite.db.rls import _resolve_org_id
    assert _resolve_org_id() == org_b.id


def test_background_context_rejects_dirty_session(tenant_context):
    db.session.add(Volunteer(organization_id=tenant_context[0].id, name="Pending"))
    with pytest.raises(RuntimeError, match="clean session"), background_tenant_context(
        tenant_context[0].id, actor="worker", reason="test", audit=lambda _: None,
    ):
        pass


def test_raw_sql_is_explicitly_not_protected_by_orm_listener(tenant_context):
    org_a, org_b = tenant_context
    rows = [Volunteer(organization_id=org.id, name="Raw boundary") for org in tenant_context]
    db.session.add_all(rows)
    db.session.commit()
    g.organization_id = org_a.id
    assert org_b.id in db.session.execute(text("SELECT organization_id FROM volunteers")).scalars().all()
