from __future__ import annotations

import json

import pytest

from ngo_homesuite.compliance.evidence_pack import build_compliance_evidence
from ngo_homesuite.models.core import Organization, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


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


def test_compliance_evidence_user_count_scoped_by_organization(app):
    with app.app_context():
        org_a = Organization(name="Evidence Org A", slug="evidence-org-a", is_active=True)
        org_b = Organization(name="Evidence Org B", slug="evidence-org-b", is_active=True)
        db.session.add_all([org_a, org_b])
        db.session.flush()

        user_a = User(username="evidence_user_a", email="a@example.org", role="admin", is_active=True, organization_id=org_a.id)
        user_a.set_password("pass123")
        user_b1 = User(username="evidence_user_b1", email="b1@example.org", role="admin", is_active=True, organization_id=org_b.id)
        user_b1.set_password("pass123")
        user_b2 = User(username="evidence_user_b2", email="b2@example.org", role="staff", is_active=True, organization_id=org_b.id)
        user_b2.set_password("pass123")
        db.session.add_all([user_a, user_b1, user_b2])
        db.session.commit()

        evidence_all = build_compliance_evidence(app, organization_id=None)
        evidence_a = build_compliance_evidence(app, organization_id=org_a.id)
        evidence_b = build_compliance_evidence(app, organization_id=org_b.id)

        assert evidence_all["data_inventory"]["users"] >= 3
        assert evidence_a["data_inventory"]["users"] == 1
        assert evidence_b["data_inventory"]["users"] == 2
