from __future__ import annotations

from html.parser import HTMLParser

import pytest
from flask import g
from flask_login import login_user
from sqlalchemy import select

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.audit.security_events import SecurityAuditEvent
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donation, Donor, Organization, Task, User, db
from ngo_homesuite.services.donation_service import DonationService


class _CSRFParser(HTMLParser):
    token: str = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name", "").endswith("csrf_token"):
            self.token = attrs.get("value", "")


def _csrf(response):
    parser = _CSRFParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.token, "The real form must issue a signed CSRF token"
    return parser.token


@pytest.fixture(scope="module")
def boundary_app():
    class BoundaryConfig(TestingConfig):
        ENABLE_DEMO_SEED = False
        WTF_CSRF_ENABLED = True
        ROLES_REQUIRING_2FA = ()

    app = create_app(BoundaryConfig)
    with app.app_context():
        orgs = [
            Organization(name=f"Boundary {name}", slug=f"boundary-{name}")
            for name in ("a", "b")
        ]
        db.session.add_all(orgs)
        db.session.flush()
        ids = {"org_a": orgs[0].id, "org_b": orgs[1].id}
        for name, org in zip(("a", "b"), orgs):
            donor = Donor(name=f"PRIVATE_DONOR_{name.upper()}", organization_id=org.id)
            db.session.add(donor)
            db.session.flush()
            ids[f"donor_{name}"] = donor.id
        for name, role, org_id in (
            ("staff_a", "staff", orgs[0].id),
            ("staff_b", "staff", orgs[1].id),
            ("viewer_a", "viewer", orgs[0].id),
            ("unassigned_viewer", "viewer", None),
            ("unassigned_staff", "staff", None),
            ("unassigned_admin", "admin", None),
        ):
            user = User(
                username=name,
                email=f"{name}@example.org",
                role=role,
                organization_id=org_id,
            )
            user.set_password("BoundaryTest123!")
            db.session.add(user)
            db.session.flush()
            ids[name] = user.id
        db.session.commit()
        app.config["BOUNDARY_IDS"] = ids
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _client(app, actor):
    client = app.test_client()
    token = _csrf(client.get("/auth/login"))
    with client.session_transaction() as session:
        session["_user_id"] = str(app.config["BOUNDARY_IDS"][actor])
        session["_fresh"] = True
    return client, token


def _task(token):
    return {
        "csrf_token": token,
        "task-csrf_token": token,
        "task-submit": "Create Task",
        "task-title": "Unauthorized stewardship",
        "task-task_type": "follow_up",
        "task-priority": "medium",
    }


def _donation(token):
    return {
        "csrf_token": token,
        "submit": "Record Donation",
        "amount": "123.45",
        "currency": "USD",
        "payment_method": "cash",
        "project_id": "0",
        "fund_id": "0",
        "reference_number": "boundary-profile-payment",
    }


def _counts(app):
    with app.app_context():
        return (
            Donation.query.count(),
            Task.query.count(),
            SecurityAuditEvent.query.count(),
        )


def _workflow(app):
    client, token = _client(app, "staff_b")
    org = str(app.config["BOUNDARY_IDS"]["org_b"])
    headers = {"X-CSRFToken": token}
    created = client.post(
        "/api/v1/workflows/instances",
        json={"org_id": org, "workflow_type": "case_intake"},
        headers=headers,
    )
    assert created.status_code == 200
    instance = created.json["instance"]["instance_id"]
    submitted = client.post(
        f"/api/v1/workflows/instances/{instance}/events",
        json={
            "org_id": org,
            "event_type": "intake_submit",
            "payload": {"case_id": "PRIVATE_CASE_B"},
        },
        headers=headers,
    )
    assert submitted.status_code == 200
    return instance


def test_public_signup_does_not_grant_tenant_access(boundary_app):
    app = boundary_app
    client = app.test_client()
    token = _csrf(client.get("/auth/register"))
    registered = client.post(
        "/auth/register",
        data={
            "csrf_token": token,
            "username": "public_boundary_signup",
            "email": "public_boundary@example.org",
            "password": "BoundaryTest123!",
            "password_confirm": "BoundaryTest123!",
        },
    )
    assert registered.status_code == 302
    with app.app_context():
        user = User.query.filter_by(username="public_boundary_signup").one()
        assert user.organization_id is None
        assert user.role == "viewer"
    token = _csrf(client.get("/auth/login"))
    login = client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "username": "public_boundary_signup",
            "password": "BoundaryTest123!",
        },
    )
    assert login.status_code == 302
    token = _csrf(client.get("/auth/mfa/setup"))
    before = _counts(app)
    donor = app.config["BOUNDARY_IDS"]["donor_a"]
    assert client.get("/donors").status_code == 403
    assert client.get(f"/donors/{donor}").status_code == 403
    assert client.post(f"/donors/{donor}", data=_task(token)).status_code == 403
    assert client.post(f"/donors/{donor}", data=_donation(token)).status_code == 403
    assert _counts(app) == before


@pytest.mark.parametrize(
    "actor", ["unassigned_viewer", "unassigned_staff", "unassigned_admin"]
)
def test_unassigned_roles_cannot_read_either_tenants_audit_or_trace(
    boundary_app, actor
):
    app = boundary_app
    instance = _workflow(app)
    client, token = _client(app, actor)
    before = _counts(app)
    donor = app.config["BOUNDARY_IDS"]["donor_b"]
    assert client.get("/donors").status_code == 403
    assert client.post(f"/donors/{donor}", data=_task(token)).status_code == 403
    assert client.post(f"/donors/{donor}", data=_donation(token)).status_code == 403
    for org in ("org_a", "org_b"):
        response = client.get(
            f"/api/v1/audit/events?org_id={app.config['BOUNDARY_IDS'][org]}"
        )
        assert response.status_code == 403
        assert "PRIVATE_CASE_B" not in response.get_data(as_text=True)
    assert (
        client.get(f"/api/v1/workflows/instances/{instance}/trace").status_code == 403
    )
    assert (
        client.post(
            "/api/v1/workflows/instances",
            json={
                "org_id": str(app.config["BOUNDARY_IDS"]["org_b"]),
                "workflow_type": "case_intake",
            },
            headers={"X-CSRFToken": token},
        ).status_code
        == 403
    )
    assert _counts(app) == before


@pytest.mark.parametrize("form", [_task, _donation])
def test_assigned_viewer_can_read_but_cannot_mutate_donor_profile(boundary_app, form):
    app = boundary_app
    client, token = _client(app, "viewer_a")
    donor = app.config["BOUNDARY_IDS"]["donor_a"]
    profile = client.get(f"/donors/{donor}")
    assert profile.status_code == 200
    assert 'id="quick-donation"' not in profile.get_data(as_text=True)
    assert 'id="quick-stewardship-task"' not in profile.get_data(as_text=True)
    before = _counts(app)
    assert client.post(f"/donors/{donor}", data=form(token)).status_code == 403
    assert _counts(app) == before


def test_assigned_staff_still_create_tasks_and_donations_with_csrf(boundary_app):
    app = boundary_app
    client, token = _client(app, "staff_a")
    donor = app.config["BOUNDARY_IDS"]["donor_a"]
    before = _counts(app)
    profile = client.get(f"/donors/{donor}")
    assert 'id="quick-donation"' in profile.get_data(as_text=True)
    assert 'id="quick-stewardship-task"' in profile.get_data(as_text=True)
    assert client.post(f"/donors/{donor}", data=_task(token)).status_code == 302
    assert client.post(f"/donors/{donor}", data=_donation(token)).status_code == 302
    after = _counts(app)
    assert after[0] == before[0] + 1
    assert after[1] == before[1] + 1
    with app.app_context():
        donation = Donation.query.filter_by(
            reference_number="boundary-profile-payment"
        ).one()
        assert donation.organization_id == app.config["BOUNDARY_IDS"]["org_a"]
        assert donation.status == "processed"


def test_assigned_staff_cannot_read_or_write_other_tenant(boundary_app):
    app = boundary_app
    instance = _workflow(app)
    client, token = _client(app, "staff_a")
    donor = app.config["BOUNDARY_IDS"]["donor_b"]
    before = _counts(app)
    assert client.get(f"/donors/{donor}").status_code == 404
    assert client.post(f"/donors/{donor}", data=_task(token)).status_code == 404
    assert client.post(f"/donors/{donor}", data=_donation(token)).status_code == 404
    org_b = app.config["BOUNDARY_IDS"]["org_b"]
    assert client.get(f"/api/v1/audit/events?org_id={org_b}").status_code == 403
    assert (
        client.get(f"/api/v1/workflows/instances/{instance}/trace").status_code == 404
    )
    assert _counts(app) == before
    owner, _token = _client(app, "staff_b")
    assert owner.get(f"/api/v1/workflows/instances/{instance}/trace").status_code == 200
    assert owner.get(f"/api/v1/audit/events?org_id={org_b}").status_code == 200


def test_unassigned_actor_cannot_commit_financial_service_write(boundary_app):
    app = boundary_app
    before = _counts(app)
    with app.test_request_context():
        user = db.session.get(User, app.config["BOUNDARY_IDS"]["unassigned_viewer"])
        login_user(user)
        with pytest.raises((ValueError, PermissionError)):
            DonationService().create_donation(
                app.config["BOUNDARY_IDS"]["org_b"],
                "Unauthorized",
                10,
                reference_number="unassigned-service-write",
            )
    assert _counts(app) == before


def test_rls_with_missing_tenant_returns_no_rows_and_rejects_writes(boundary_app):
    app = boundary_app
    with app.test_request_context():
        user = db.session.get(User, app.config["BOUNDARY_IDS"]["unassigned_viewer"])
        login_user(user)
        app.preprocess_request()
        assert g.organization_id is None
        assert list(db.session.scalars(select(Donor))) == []
        db.session.add(
            Donor(name="Forbidden", organization_id=app.config["BOUNDARY_IDS"]["org_b"])
        )
        try:
            with pytest.raises(PermissionError, match="tenant"):
                db.session.flush()
        finally:
            db.session.rollback()
