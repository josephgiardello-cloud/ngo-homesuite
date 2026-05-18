from __future__ import annotations

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import User, db
from ngo_homesuite.web.auth_routes import _issue_password_reset_token


class _RateLimitedTestingConfig(TestingConfig):
    RATELIMIT_ENABLED = True


def _ensure_user(app, username: str, email: str, password: str) -> User:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, email=email, role="staff", is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        return user


def test_login_accepts_email_identifier():
    app = create_app(TestingConfig)
    client = app.test_client()
    _ensure_user(app, "auth_email_login", "auth_email_login@test.local", "AuthPass123!")

    rv = client.post(
        "/auth/login",
        data={"username": "auth_email_login@test.local", "password": "AuthPass123!"},
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert "/dashboard" in (rv.headers.get("Location") or "")


def test_password_forgot_does_not_enumerate_accounts():
    app = create_app(TestingConfig)
    client = app.test_client()

    rv = client.post(
        "/auth/password/forgot",
        data={"email": "missing-user@example.com"},
        follow_redirects=True,
    )

    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="ignore")
    assert "If an account exists for that email" in body


def test_password_reset_token_allows_setting_new_password():
    app = create_app(TestingConfig)
    client = app.test_client()

    _ensure_user(app, "auth_reset_user", "auth_reset_user@test.local", "OldPass123!")
    with app.app_context():
        user = User.query.filter_by(username="auth_reset_user").first()
        assert user is not None
        token = _issue_password_reset_token(user)

    rv = client.post(
        f"/auth/password/reset/{token}",
        data={"password": "NewPass456!", "password_confirm": "NewPass456!"},
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert "/auth/login" in (rv.headers.get("Location") or "")

    with app.app_context():
        updated = User.query.filter_by(username="auth_reset_user").first()
        assert updated is not None
        assert updated.check_password("NewPass456!")


def test_login_rate_limit_blocks_repeated_attempts():
    app = create_app(_RateLimitedTestingConfig)
    client = app.test_client()

    _ensure_user(app, "auth_rl_user", "auth_rl_user@test.local", "RightPass123!")

    last_status = None
    for _ in range(12):
        rv = client.post(
            "/auth/login",
            data={"username": "auth_rl_user", "password": "WrongPass999!"},
            follow_redirects=False,
        )
        last_status = rv.status_code
        if rv.status_code == 429:
            break

    assert last_status == 429
