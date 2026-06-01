from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donor, User, db


@pytest.fixture(scope="module")
def app():
    class _Cfg(TestingConfig):
        SECRET_KEY = "test-events"
        ROLES_REQUIRING_2FA = []

    return create_app(_Cfg)


@pytest.fixture()
def client(app):
    return app.test_client()


def _login_admin(client) -> None:
    rv = client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin123!"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)


def _admin_org_id(app) -> int:
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        assert user is not None
        assert user.organization_id is not None
        return int(user.organization_id)


def _event_id_by_name(app, name: str) -> int:
    with app.app_context():
        row = db.session.execute(
            text("SELECT id FROM events WHERE name = :name AND deleted_at IS NULL ORDER BY id DESC LIMIT 1"),
            {"name": name},
        ).mappings().first()
        assert row is not None
        return int(row["id"])


def test_events_board_renders_management_controls(client):
    _login_admin(client)
    rv = client.get("/events")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Event management" in body
    assert "Create event" in body


def test_event_create_update_and_reminders_flow(client, app, monkeypatch):
    _login_admin(client)
    create_response = client.post(
        "/api/events",
        json={
            "name": "Spring Benefit 2026",
            "description": "Annual donor gathering",
            "start_date": "2026-09-15T18:00:00",
            "end_date": "2026-09-15T21:00:00",
        },
    )
    assert create_response.status_code == 201

    event_id = _event_id_by_name(app, "Spring Benefit 2026")
    update_response = client.post(
        f"/events/{event_id}/update",
        data={
            "name": "Spring Benefit 2026 Updated",
            "description": "Annual donor gathering",
            "start_date": "2026-09-16T18:00:00",
            "end_date": "2026-09-16T21:00:00",
        },
        follow_redirects=False,
    )
    assert update_response.status_code in (302, 303)

    event_id = _event_id_by_name(app, "Spring Benefit 2026 Updated")
    org_id = _admin_org_id(app)

    with app.app_context():
        donor = Donor(organization_id=org_id, name="Event Attendee", email="attendee@example.org", donor_type="individual")
        db.session.add(donor)
        db.session.commit()
        db.session.execute(
            text(
                """
                INSERT INTO registrations(event_id, donor_id, registered_at, updated_at)
                VALUES (:event_id, :donor_id, :registered_at, :updated_at)
                """
            ),
            {
                "event_id": event_id,
                "donor_id": donor.id,
                "registered_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            },
        )
        db.session.commit()

    monkeypatch.setattr("ngo_homesuite.web.events_routes.send_event_reminder", lambda *_args, **_kwargs: True)

    reminder_response = client.post(f"/api/events/{event_id}/send-reminders")
    assert reminder_response.status_code == 200
    reminder_payload = reminder_response.get_json()
    assert reminder_payload["sent"] == 1
    assert reminder_payload["total"] == 1

    registrations_response = client.get(f"/api/events/{event_id}/registrations")
    assert registrations_response.status_code == 200
    registrations_payload = registrations_response.get_json()
    assert registrations_payload["count"] == 1
