from __future__ import annotations

import json

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


def _ensure_user(app, username: str, email: str, role: str, password: str):
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, email=email, role=role, is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()


def _ensure_user_in_org(app, username: str, email: str, role: str, password: str, org_name: str, org_slug: str):
    with app.app_context():
        org = Organization.query.filter_by(slug=org_slug).first()
        if org is None:
            org = Organization(name=org_name, slug=org_slug, is_active=True)
            db.session.add(org)
            db.session.flush()

        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, email=email, role=role, is_active=True, organization_id=org.id)
            user.set_password(password)
            db.session.add(user)
        else:
            user.organization_id = org.id
        db.session.commit()


def test_compliance_evidence_endpoint_returns_json_payload(client, app):
    _ensure_user(app, "compliance_admin", "compliance_admin@test.local", "admin", "admin_pass_123")
    _login(client, "compliance_admin", "admin_pass_123")

    rv = client.get("/reports/compliance/evidence?scope=org")
    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data, dict)
    assert data.get("schema") == "ngohs-compliance-evidence-v1"
    assert "security_posture" in data
    assert "data_inventory" in data
    assert "sha256" in data


def test_compliance_evidence_endpoint_download(client, app):
    _ensure_user(app, "compliance_staff", "compliance_staff@test.local", "staff", "staff_pass_123")
    _login(client, "compliance_staff", "staff_pass_123")

    rv = client.get("/reports/compliance/evidence?scope=org&download=1")
    assert rv.status_code == 200
    assert rv.mimetype == "application/json"
    disposition = rv.headers.get("Content-Disposition", "")
    assert "attachment" in disposition

    payload = json.loads(rv.data.decode("utf-8"))
    assert payload["schema"] == "ngohs-compliance-evidence-v1"


def test_compliance_evidence_global_scope_denied_for_org_users(client, app):
    _ensure_user_in_org(
        app,
        "compliance_org_staff",
        "compliance_org_staff@test.local",
        "staff",
        "staff_pass_123",
        "Compliance Org",
        "compliance-org",
    )
    _login(client, "compliance_org_staff", "staff_pass_123")

    rv = client.get("/reports/compliance/evidence?scope=global")
    assert rv.status_code == 403
    data = rv.get_json()
    assert data is not None
    assert "Global compliance scope" in data["error"]
