from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Organization
from ngo_homesuite.persistence.write_context import enter_write_gate, exit_write_gate
from ngo_homesuite.workflow_engine import WorkflowInstance
from ngo_homesuite.workflow_engine.instance import WorkflowStatus


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


def test_workflow_repository_optimistic_lock_conflict(app):
    with app.app_context():
        container = app.extensions["v2_container"]
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None

        created = container.create_workflow_instance(org_id=str(org.id), workflow_type="case_intake")
        repository = container.workflow_repository

        stale_a = repository.get(created.instance_id, org_id=str(org.id))
        stale_b = repository.get(created.instance_id, org_id=str(org.id))
        assert stale_a is not None and stale_b is not None

        stale_a.current_step = "verification"
        stale_a.history.append({"event_type": "synthetic_a", "payload": {}, "from_step": "intake", "to_step": "verification"})
        with container.write_gate.uow.transaction() as tx:
            token = enter_write_gate()
            try:
                saved_a = repository.save(stale_a, uow=tx)
            finally:
                exit_write_gate(token)
        assert saved_a.version >= 2

        stale_b.current_step = "assignment"
        stale_b.history.append({"event_type": "synthetic_b", "payload": {}, "from_step": "intake", "to_step": "assignment"})

        with container.write_gate.uow.transaction() as tx:
            token = enter_write_gate()
            try:
                with pytest.raises(RuntimeError, match="Optimistic lock conflict"):
                    repository.save(stale_b, uow=tx)
            finally:
                exit_write_gate(token)


def test_workflow_repository_rejects_cross_org_instance_collision(app):
    with app.app_context():
        container = app.extensions["v2_container"]
        repository = app.extensions["v2_container"].workflow_repository
        org_a = Organization.query.filter_by(is_active=True).first()
        assert org_a is not None
        org_b = Organization(name="Workflow Collision Org B", slug="workflow-collision-org-b", is_active=True)
        from ngo_homesuite.models.core import db

        db.session.add(org_b)
        db.session.flush()

        shared_id = "workflow-collision-test-123"
        with container.write_gate.uow.transaction() as tx:
            token = enter_write_gate()
            try:
                saved_a = repository.save(
                    WorkflowInstance(
                        instance_id=shared_id,
                        org_id=str(org_a.id),
                        workflow_type="case_intake",
                        current_step="step_one",
                        status=WorkflowStatus.ACTIVE,
                    ),
                    uow=tx,
                )
            finally:
                exit_write_gate(token)
        assert saved_a.current_step == "step_one"

        with container.write_gate.uow.transaction() as tx:
            token = enter_write_gate()
            try:
                with pytest.raises(PermissionError, match="Cross-tenant workflow instance collision detected"):
                    repository.save(
                        WorkflowInstance(
                            instance_id=shared_id,
                            org_id=str(org_b.id),
                            workflow_type="case_intake",
                            current_step="step_two",
                            status=WorkflowStatus.ACTIVE,
                        ),
                        uow=tx,
                    )
            finally:
                exit_write_gate(token)

        fetched_a = repository.get(shared_id, org_id=str(org_a.id))
        assert fetched_a is not None
        assert fetched_a.current_step == "step_one"
