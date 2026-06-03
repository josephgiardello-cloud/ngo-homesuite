from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Beneficiary, Organization, User, Volunteer, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()



def _ensure_user(app, username: str, email: str, role: str, password: str) -> int:
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        if org is None:
            org = Organization(name="Mobile Intake Org", slug="mobile-intake-org", is_active=True)
            db.session.add(org)
            db.session.flush()

        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, email=email, role=role, is_active=True, organization_id=org.id)
            user.set_password(password)
            db.session.add(user)
        else:
            user.organization_id = org.id
            user.role = role
        db.session.commit()
        return int(org.id)



def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})



def test_mobile_intake_page_renders(client, app):
    _ensure_user(app, "mobile_staff", "mobile_staff@test.local", "staff", "mobile_staff_pass_123")
    _login(client, "mobile_staff", "mobile_staff_pass_123")

    rv = client.get("/mobile/intake")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Mobile Intake and Volunteer Check-In" in body
    assert "Quick Beneficiary Intake" in body
    assert "Volunteer Quick Registration" in body
    assert "viewport" in body
    assert "width=device-width" in body



def test_mobile_intake_creates_beneficiary(client, app):
    org_id = _ensure_user(app, "mobile_staff_2", "mobile_staff_2@test.local", "staff", "mobile_staff_pass_234")
    _login(client, "mobile_staff_2", "mobile_staff_pass_234")

    before = 0
    with app.app_context():
        before = Beneficiary.query.filter_by(organization_id=org_id).count()

    rv = client.post(
        "/mobile/intake",
        data={
            "action": "beneficiary",
            "first_name": "Mina",
            "last_name": "Lopez",
            "phone": "+1-555-3000",
            "city": "Austin",
            "program": "Education",
            "status": "active",
            "notes": "Captured via mobile field intake",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200

    with app.app_context():
        after = Beneficiary.query.filter_by(organization_id=org_id).count()
        assert after == before + 1



def test_mobile_intake_creates_volunteer(client, app):
    org_id = _ensure_user(app, "mobile_volunteer", "mobile_volunteer@test.local", "volunteer", "mobile_volunteer_pass_123")
    _login(client, "mobile_volunteer", "mobile_volunteer_pass_123")

    with app.app_context():
        before = Volunteer.query.filter_by(organization_id=org_id).count()

    rv = client.post(
        "/mobile/intake",
        data={
            "action": "volunteer",
            "volunteer_name": "Noel Santos",
            "volunteer_email": "noel.santos@example.org",
            "volunteer_phone": "+1-555-3111",
            "volunteer_status": "active",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200

    with app.app_context():
        after = Volunteer.query.filter_by(organization_id=org_id).count()
        assert after == before + 1



def test_mobile_intake_validates_required_fields(client, app):
    _ensure_user(app, "mobile_staff_3", "mobile_staff_3@test.local", "staff", "mobile_staff_pass_345")
    _login(client, "mobile_staff_3", "mobile_staff_pass_345")

    rv = client.post(
        "/mobile/intake",
        data={"action": "beneficiary", "first_name": "Only"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert "Beneficiary first and last name are required." in rv.get_data(as_text=True)


def test_mobile_intake_invalid_volunteer_submission_does_not_persist(client, app):
    org_id = _ensure_user(app, "mobile_staff_4", "mobile_staff_4@test.local", "staff", "mobile_staff_pass_456")
    _login(client, "mobile_staff_4", "mobile_staff_pass_456")

    with app.app_context():
        before = Volunteer.query.filter_by(organization_id=org_id).count()

    rv = client.post(
        "/mobile/intake",
        data={"action": "volunteer", "volunteer_email": "missing.name@example.org"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert "Volunteer name is required." in rv.get_data(as_text=True)

    with app.app_context():
        after = Volunteer.query.filter_by(organization_id=org_id).count()
        assert after == before


def test_mobile_intake_rejects_unsupported_action_without_persistence(client, app):
    org_id = _ensure_user(app, "mobile_staff_5", "mobile_staff_5@test.local", "staff", "mobile_staff_pass_567")
    _login(client, "mobile_staff_5", "mobile_staff_pass_567")

    with app.app_context():
        beneficiary_before = Beneficiary.query.filter_by(organization_id=org_id).count()
        volunteer_before = Volunteer.query.filter_by(organization_id=org_id).count()

    rv = client.post(
        "/mobile/intake",
        data={"action": "surprise", "first_name": "Ignored", "volunteer_name": "Ignored"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert "Unsupported intake action." in rv.get_data(as_text=True)

    with app.app_context():
        beneficiary_after = Beneficiary.query.filter_by(organization_id=org_id).count()
        volunteer_after = Volunteer.query.filter_by(organization_id=org_id).count()
        assert beneficiary_after == beneficiary_before
        assert volunteer_after == volunteer_before
