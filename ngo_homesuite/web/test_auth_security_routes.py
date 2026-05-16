from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import User, db


@pytest.fixture(scope="module")
def app():
    return create_app(TestingConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


def _ensure_user(app, username: str, password: str) -> None:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=f"{username}@test.local",
                role="staff",
                is_active=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()


def test_login_rejects_absolute_next_url(client, app):
    _ensure_user(app, "auth_sec_user1", "AuthPass123!")

    rv = client.post(
        "/auth/login?next=https://evil.example/phish",
        data={"username": "auth_sec_user1", "password": "AuthPass123!"},
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert "/dashboard" in rv.headers.get("Location", "")


def test_login_rejects_scheme_relative_next_url(client, app):
    _ensure_user(app, "auth_sec_user2", "AuthPass123!")

    rv = client.post(
        "/auth/login?next=//evil.example/phish",
        data={"username": "auth_sec_user2", "password": "AuthPass123!"},
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert "/dashboard" in rv.headers.get("Location", "")


def test_login_rejects_encoded_backslash_next_url(client, app):
    _ensure_user(app, "auth_sec_user2b", "AuthPass123!")

    rv = client.post(
        "/auth/login?next=/%5Cevil.example/phish",
        data={"username": "auth_sec_user2b", "password": "AuthPass123!"},
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert "/dashboard" in rv.headers.get("Location", "")


def test_login_accepts_safe_relative_next_path(client, app):
    _ensure_user(app, "auth_sec_user3", "AuthPass123!")

    rv = client.post(
        "/auth/login?next=/reports",
        data={"username": "auth_sec_user3", "password": "AuthPass123!"},
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert rv.headers.get("Location", "").endswith("/reports")


def test_login_clears_pre_auth_session_state(client, app):
    _ensure_user(app, "auth_sec_user4", "AuthPass123!")

    with client.session_transaction() as sess:
        sess["pre_auth_marker"] = "persisted-before-login"

    rv = client.post(
        "/auth/login",
        data={"username": "auth_sec_user4", "password": "AuthPass123!"},
        follow_redirects=False,
    )
    assert rv.status_code == 302

    with client.session_transaction() as sess:
        assert "pre_auth_marker" not in sess
        assert sess.get("_user_id") is not None


def test_security_headers_present_on_web_response(client):
    rv = client.get("/")

    assert rv.status_code == 200
    assert rv.headers.get("X-Content-Type-Options") == "nosniff"
    assert rv.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert rv.headers.get("X-Permitted-Cross-Domain-Policies") == "none"
    assert rv.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
    assert rv.headers.get("Cross-Origin-Resource-Policy") == "same-origin"
    assert "Content-Security-Policy" in rv.headers
