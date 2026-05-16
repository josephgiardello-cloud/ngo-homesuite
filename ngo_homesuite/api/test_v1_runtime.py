from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Organization, User, db
from ngo_homesuite.persistence.models.workflow_tables import WorkflowDefinitionRecord, WorkflowEventRecord, WorkflowInstanceRecord


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _ensure_user(app, username: str, email: str, role: str, password: str) -> None:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        org = Organization.query.filter_by(is_active=True).first()
        if user is None:
            user = User(
                username=username,
                email=email,
                role=role,
                is_active=True,
                organization_id=org.id if org else None,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()


def test_v1_workflow_runtime_happy_path(client, app):
    _ensure_user(app, "v2_admin", "v2_admin@test.local", "admin", "v2_admin_pass_123")
    _login(client, "v2_admin", "v2_admin_pass_123")

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        org_id = str(org.id)

    workflows = client.get("/api/v1/workflows")
    assert workflows.status_code == 200
    assert "case_intake" in workflows.get_json()["workflow_types"]

    create_instance = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": org_id, "workflow_type": "case_intake"},
    )
    assert create_instance.status_code == 200
    instance_id = create_instance.get_json()["instance"]["instance_id"]

    submit = client.post(
        f"/api/v1/workflows/instances/{instance_id}/events",
        json={
            "org_id": org_id,
            "event_type": "intake_submit",
            "payload": {
                "case_id": "CASE-001",
                "email": "beneficiary@example.org",
                "phone": "+1-555-0142",
            },
        },
    )
    assert submit.status_code == 200
    assert submit.get_json()["instance"]["current_step"] == "verification"
    history = submit.get_json()["instance"]["history"]
    assert history[0]["payload"]["email"].startswith("[REDACTED")
    assert history[0]["payload"]["phone"].startswith("[REDACTED")

    trace = client.get(f"/api/v1/workflows/instances/{instance_id}/trace")
    assert trace.status_code == 200
    assert len(trace.get_json()["trace"]["steps"]) >= 1

    audit = client.get(f"/api/v1/audit/events?org_id={org_id}")
    assert audit.status_code == 200
    events = audit.get_json()["events"]
    assert len(events) >= 1
    assert events[-1]["payload"]["email"].startswith("[REDACTED")
    assert events[-1]["payload"]["phone"].startswith("[REDACTED")

    with app.app_context():
        assert WorkflowDefinitionRecord.query.filter_by(is_active=True).count() >= 2
        assert WorkflowInstanceRecord.query.filter_by(instance_id=instance_id).count() == 1
        assert WorkflowEventRecord.query.filter_by(aggregate_id=instance_id).count() >= 1


def test_v1_workflow_creation_enforces_permissions(client, app):
    _ensure_user(app, "v2_viewer", "v2_viewer@test.local", "viewer", "v2_viewer_pass_123")
    _login(client, "v2_viewer", "v2_viewer_pass_123")

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        org_id = str(org.id)

    create_instance = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": org_id, "workflow_type": "case_intake"},
    )
    assert create_instance.status_code == 403


def test_v1_workflow_cross_tenant_access_is_blocked(client, app):
    _ensure_user(app, "v2_staff", "v2_staff@test.local", "staff", "v2_staff_pass_123")
    _login(client, "v2_staff", "v2_staff_pass_123")

    with app.app_context():
        primary_org = Organization.query.filter_by(is_active=True).first()
        other_org = Organization.query.filter_by(slug="cross-tenant-org").first()
        if other_org is None:
            other_org = Organization(name="Cross Tenant Org", slug="cross-tenant-org", is_active=True)
            db.session.add(other_org)
            db.session.commit()

        primary_org_id = str(primary_org.id)
        other_org_id = str(other_org.id)

    create_primary = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": primary_org_id, "workflow_type": "case_intake"},
    )
    assert create_primary.status_code == 200
    instance_id = create_primary.get_json()["instance"]["instance_id"]

    cross_tenant_event = client.post(
        f"/api/v1/workflows/instances/{instance_id}/events",
        json={
            "org_id": other_org_id,
            "event_type": "intake_submit",
            "payload": {"email": "should-not-pass@example.org"},
        },
    )
    assert cross_tenant_event.status_code == 403


def test_v1_workflow_event_idempotency_key_prevents_duplicate_transition(client, app):
    _ensure_user(app, "v2_admin_idem", "v2_admin_idem@test.local", "admin", "v2_admin_idem_pass_123")
    _login(client, "v2_admin_idem", "v2_admin_idem_pass_123")

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        org_id = str(org.id)

    create_instance = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": org_id, "workflow_type": "case_intake"},
    )
    assert create_instance.status_code == 200
    instance_id = create_instance.get_json()["instance"]["instance_id"]

    payload = {
        "org_id": org_id,
        "event_type": "intake_submit",
        "idempotency_key": "idem-intake-submit-001",
        "payload": {"case_id": "CASE-IDEM-001", "email": "idem@example.org"},
    }

    first = client.post(f"/api/v1/workflows/instances/{instance_id}/events", json=payload)
    assert first.status_code == 200
    first_instance = first.get_json()["instance"]
    assert first_instance["idempotent_replay"] is False
    assert len(first_instance["history"]) == 1

    replay = client.post(f"/api/v1/workflows/instances/{instance_id}/events", json=payload)
    assert replay.status_code == 200
    replay_instance = replay.get_json()["instance"]
    assert replay_instance["idempotent_replay"] is True
    assert len(replay_instance["history"]) == 1


def test_v1_workflow_event_rejects_actor_spoofing(client, app):
    _ensure_user(app, "v2_staff_actor", "v2_staff_actor@test.local", "staff", "v2_staff_actor_pass_123")
    _login(client, "v2_staff_actor", "v2_staff_actor_pass_123")

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        org_id = str(org.id)

    create_instance = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": org_id, "workflow_type": "case_intake"},
    )
    assert create_instance.status_code == 200
    instance_id = create_instance.get_json()["instance"]["instance_id"]

    spoof = client.post(
        f"/api/v1/workflows/instances/{instance_id}/events",
        json={
            "org_id": org_id,
            "event_type": "intake_submit",
            "actor_id": "999999",
            "role": "case_worker",
            "payload": {"case_id": "CASE-SPOOF-ACTOR"},
        },
    )
    assert spoof.status_code == 403
    assert "actor_id" in spoof.get_json()["error"]


def test_v1_workflow_event_rejects_role_spoofing(client, app):
    _ensure_user(app, "v2_staff_role", "v2_staff_role@test.local", "staff", "v2_staff_role_pass_123")
    _login(client, "v2_staff_role", "v2_staff_role_pass_123")

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        org_id = str(org.id)
        user = User.query.filter_by(username="v2_staff_role").first()
        user_id = str(user.id)

    create_instance = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": org_id, "workflow_type": "case_intake"},
    )
    assert create_instance.status_code == 200
    instance_id = create_instance.get_json()["instance"]["instance_id"]

    spoof = client.post(
        f"/api/v1/workflows/instances/{instance_id}/events",
        json={
            "org_id": org_id,
            "event_type": "intake_submit",
            "actor_id": user_id,
            "role": "org_admin",
            "payload": {"case_id": "CASE-SPOOF-ROLE"},
        },
    )
    assert spoof.status_code == 403
    assert "role" in spoof.get_json()["error"]


def test_v1_workflow_trace_is_tenant_scoped(client, app):
    _ensure_user(app, "v2_trace_admin", "v2_trace_admin@test.local", "admin", "v2_trace_admin_pass_123")
    _ensure_user(app, "v2_trace_staff", "v2_trace_staff@test.local", "staff", "v2_trace_staff_pass_123")

    with app.app_context():
        org_a = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        org_b = Organization.query.filter_by(slug="trace-tenant-org").first()
        if org_b is None:
            org_b = Organization(name="Trace Tenant Org", slug="trace-tenant-org", is_active=True)
            db.session.add(org_b)
            db.session.flush()

        owner = User.query.filter_by(username="v2_trace_admin").first()
        other = User.query.filter_by(username="v2_trace_staff").first()
        owner.organization_id = org_a.id
        other.organization_id = org_b.id
        db.session.commit()

        org_a_id = str(org_a.id)

    _login(client, "v2_trace_admin", "v2_trace_admin_pass_123")
    create_instance = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": org_a_id, "workflow_type": "case_intake"},
    )
    assert create_instance.status_code == 200
    instance_id = create_instance.get_json()["instance"]["instance_id"]

    submit = client.post(
        f"/api/v1/workflows/instances/{instance_id}/events",
        json={"org_id": org_a_id, "event_type": "intake_submit", "payload": {"case_id": "TRACE-001"}},
    )
    assert submit.status_code == 200

    client.post("/auth/logout")
    _login(client, "v2_trace_staff", "v2_trace_staff_pass_123")

    blocked = client.get(f"/api/v1/workflows/instances/{instance_id}/trace")
    assert blocked.status_code == 404


def test_v1_audit_events_requires_org_id(client, app):
    _ensure_user(app, "v2_audit_missing_org", "v2_audit_missing_org@test.local", "admin", "v2_audit_missing_org_pass_123")
    _login(client, "v2_audit_missing_org", "v2_audit_missing_org_pass_123")

    rv = client.get("/api/v1/audit/events")
    assert rv.status_code == 400
    assert "org_id" in rv.get_json()["error"]


def test_v1_audit_events_cross_tenant_access_is_blocked(client, app):
    _ensure_user(app, "v2_audit_owner", "v2_audit_owner@test.local", "admin", "v2_audit_owner_pass_123")

    with app.app_context():
        owner = User.query.filter_by(username="v2_audit_owner").first()
        assert owner is not None

        primary_org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        other_org = Organization.query.filter_by(slug="audit-cross-tenant-org").first()
        if other_org is None:
            other_org = Organization(name="Audit Cross Tenant Org", slug="audit-cross-tenant-org", is_active=True)
            db.session.add(other_org)
            db.session.flush()

        owner.organization_id = primary_org.id
        db.session.commit()

        other_org_id = str(other_org.id)

    _login(client, "v2_audit_owner", "v2_audit_owner_pass_123")
    blocked = client.get(f"/api/v1/audit/events?org_id={other_org_id}")

    assert blocked.status_code == 403
    assert "another tenant" in blocked.get_json()["error"]
