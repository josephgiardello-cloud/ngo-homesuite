from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Organization, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _ensure_user_with_org(app, username: str, password: str) -> None:
    with app.app_context():
        org = Organization.query.filter_by(name="UI Profile Org").first()
        if org is None:
            org = Organization(name="UI Profile Org")
            db.session.add(org)
            db.session.flush()

        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=f"{username}@test.local",
                role="admin",
                is_active=True,
                organization_id=org.id,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()


def test_ui_profile_requires_auth(client):
    rv = client.get("/api/ui/profile", follow_redirects=False)
    assert rv.status_code in (302, 303)


def test_ui_profile_get_and_patch_round_trip(client, app):
    _ensure_user_with_org(app, "ui_profile_admin", "UiProfilePass_123")
    _login(client, "ui_profile_admin", "UiProfilePass_123")

    get_rv = client.get("/api/ui/profile")
    assert get_rv.status_code == 200
    payload = get_rv.get_json()
    assert isinstance(payload, dict)
    assert "profile" in payload
    assert "urgency" in payload
    assert set(payload["urgency"].keys()) == {"tasks", "approvals", "alerts"}

    patch_payload = {
        "sidebar_collapsed_groups": {"fundraising": True, "admin": False},
        "favorites": [{"href": "/dashboard", "label": "Dashboard", "icon": "DB"}],
        "recent": [{"href": "/tasks/board", "label": "Task Board", "icon": "TB"}],
    }
    patch_rv = client.patch("/api/ui/profile", json=patch_payload)
    assert patch_rv.status_code == 200

    verify_rv = client.get("/api/ui/profile")
    assert verify_rv.status_code == 200
    verify = verify_rv.get_json()
    assert verify["profile"]["sidebar_collapsed_groups"]["fundraising"] is True
    assert verify["profile"]["favorites"][0]["href"] == "/dashboard"
    assert verify["profile"]["recent"][0]["href"] == "/tasks/board"
