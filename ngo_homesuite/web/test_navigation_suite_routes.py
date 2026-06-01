from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Organization, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _ensure_user(app, username: str, email: str, role: str, password: str) -> None:
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        if org is None:
            org = Organization(name="Navigation Test Org", slug="navigation-test-org", is_active=True)
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


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def test_calendar_deadlines_page_renders_for_staff(client, app):
    _ensure_user(app, "nav_staff_calendar", "nav_staff_calendar@test.local", "staff", "nav_staff_calendar_pass")
    _login(client, "nav_staff_calendar", "nav_staff_calendar_pass")

    rv = client.get("/calendar-deadlines")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Calendar and Deadlines" in body
    assert "Open Task Board" in body


def test_ai_assistant_hub_page_renders_for_staff(client, app):
    _ensure_user(app, "nav_staff_ai", "nav_staff_ai@test.local", "staff", "nav_staff_ai_pass")
    _login(client, "nav_staff_ai", "nav_staff_ai_pass")

    rv = client.get("/ai-assistant-hub")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "AI Assistant Hub" in body
    assert "Apex Assistant" in body


def test_integrations_hub_page_renders_for_staff(client, app):
    _ensure_user(app, "nav_staff_integrations", "nav_staff_integrations@test.local", "staff", "nav_staff_integrations_pass")
    _login(client, "nav_staff_integrations", "nav_staff_integrations_pass")

    rv = client.get("/integrations-hub")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Integrations Hub" in body
    assert "Open Status JSON" in body


def test_integrations_hub_forbidden_for_volunteer(client, app):
    _ensure_user(app, "nav_volunteer_integrations", "nav_volunteer_integrations@test.local", "volunteer", "nav_volunteer_integrations_pass")
    _login(client, "nav_volunteer_integrations", "nav_volunteer_integrations_pass")

    rv = client.get("/integrations-hub")
    assert rv.status_code in (302, 403)
