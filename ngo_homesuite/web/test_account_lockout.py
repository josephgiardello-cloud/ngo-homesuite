"""
Account lockout and login hardening tests.

Tests the security properties introduced by the account lockout feature:
- After 5 consecutive failed logins the account is temporarily locked.
- A locked account rejects login even with correct password.
- A successful login resets the failure counter.
- Inactive users are still rejected (existing behaviour, preserved).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ngo_homesuite.models.core import User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _make_user(app, username: str, password: str, role: str = "staff") -> None:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=f"{username}@lockout.test.local",
                role=role,
                is_active=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        else:
            # Reset state for idempotent test runs
            user.failed_login_count = 0
            user.locked_until = None
            user.is_active = True
            db.session.commit()


def _attempt_login(client, username: str, password: str):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Lockout tests
# ---------------------------------------------------------------------------

def test_account_locks_after_max_failed_attempts(client, app):
    _make_user(app, "lock_test_user1", "CorrectPass123!")

    # Exhaust the allowed attempts (5 failures)
    for _ in range(5):
        rv = _attempt_login(client, "lock_test_user1", "WrongPassword!")
        assert rv.status_code == 302

    # Now the account should be locked; even correct password fails
    rv = _attempt_login(client, "lock_test_user1", "CorrectPass123!")
    assert rv.status_code == 302
    location = rv.headers.get("Location", "")
    assert "/auth/login" in location

    # Verify locked_until is set in DB
    with app.app_context():
        user = User.query.filter_by(username="lock_test_user1").first()
        assert user.locked_until is not None
        assert user.locked_until > datetime.now(timezone.utc).replace(tzinfo=None)


def test_successful_login_resets_failure_counter(client, app):
    _make_user(app, "lock_test_user2", "CorrectPass123!")

    # Partially exhaust (below threshold)
    for _ in range(3):
        _attempt_login(client, "lock_test_user2", "WrongPassword!")

    # Successful login
    rv = _attempt_login(client, "lock_test_user2", "CorrectPass123!")
    assert rv.status_code == 302
    assert "/dashboard" in rv.headers.get("Location", "")

    # Verify counter reset
    with app.app_context():
        user = User.query.filter_by(username="lock_test_user2").first()
        assert user.failed_login_count == 0
        assert user.locked_until is None

    # Logout
    client.post("/auth/logout")


def test_locked_account_shows_lockout_message(client, app):
    _make_user(app, "lock_test_user3", "CorrectPass123!")

    # Force lockout via DB
    with app.app_context():
        user = User.query.filter_by(username="lock_test_user3").first()
        user.failed_login_count = 5
        user.locked_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)
        db.session.commit()

    rv = client.post(
        "/auth/login",
        data={"username": "lock_test_user3", "password": "CorrectPass123!"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    # Page should mention lockout (check flash message rendered in body)
    assert b"locked" in rv.data.lower() or b"minute" in rv.data.lower()


def test_expired_lockout_allows_login(client, app):
    _make_user(app, "lock_test_user4", "CorrectPass123!")

    # Force an expired lock (lockout in the past)
    with app.app_context():
        user = User.query.filter_by(username="lock_test_user4").first()
        user.failed_login_count = 5
        user.locked_until = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.session.commit()

    # Login should succeed because lockout has expired
    rv = _attempt_login(client, "lock_test_user4", "CorrectPass123!")
    assert rv.status_code == 302
    assert "/dashboard" in rv.headers.get("Location", "")
    client.post("/auth/logout")


def test_failed_login_for_nonexistent_user_does_not_crash(client, app):
    """Login with unknown username should fail gracefully (no 500)."""
    rv = _attempt_login(client, "completely_unknown_xyz987", "AnyPassword!")
    assert rv.status_code == 302
    assert "/auth/login" in rv.headers.get("Location", "")


def test_inactive_user_cannot_login(client, app):
    _make_user(app, "lock_test_inactive_user", "CorrectPass123!")
    with app.app_context():
        user = User.query.filter_by(username="lock_test_inactive_user").first()
        user.is_active = False
        db.session.commit()

    rv = _attempt_login(client, "lock_test_inactive_user", "CorrectPass123!")
    assert rv.status_code == 302
    assert "/auth/login" in rv.headers.get("Location", "")
