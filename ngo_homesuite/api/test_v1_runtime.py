from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Organization, User, db


@pytest.fixture(scope="module")
def app():
    return create_app(TestingConfig)


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
            "actor_id": "u_admin",
            "role": "org_admin",
            "payload": {"case_id": "CASE-001"},
        },
    )
    assert submit.status_code == 200
    assert submit.get_json()["instance"]["current_step"] == "verification"

    trace = client.get(f"/api/v1/workflows/instances/{instance_id}/trace")
    assert trace.status_code == 200
    assert len(trace.get_json()["trace"]["steps"]) >= 1

    audit = client.get(f"/api/v1/audit/events?org_id={org_id}")
    assert audit.status_code == 200
    assert len(audit.get_json()["events"]) >= 1


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
