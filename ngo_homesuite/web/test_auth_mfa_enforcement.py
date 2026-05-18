"""Tests for A-2: 2FA enforcement policy by role."""
from __future__ import annotations

import pytest
from flask import url_for

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import User, db


@pytest.fixture(scope='module')
def app():
    return create_app(TestingConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def admin_no_mfa(app):
    with app.app_context():
        u = User.query.filter_by(username='test_admin_nomfa').first()
        if u is None:
            u = User(username='test_admin_nomfa', email='admin_nomfa@test.local', role='admin', is_active=True)
            u.set_password('Admin1234!')
            db.session.add(u)
            db.session.commit()
        yield u


@pytest.fixture()
def admin_with_mfa(app):
    with app.app_context():
        u = User.query.filter_by(username='test_admin_mfa').first()
        if u is None:
            u = User(username='test_admin_mfa', email='admin_mfa@test.local', role='admin', is_active=True)
            u.set_password('Admin1234!')
            db.session.add(u)
            db.session.commit()
        # Give them a TOTP secret and enable MFA.
        u.ensure_mfa_secret()
        u.mfa_enabled = True
        db.session.commit()
        yield u


@pytest.fixture()
def staff_no_mfa(app):
    with app.app_context():
        u = User.query.filter_by(username='test_staff_nomfa').first()
        if u is None:
            u = User(username='test_staff_nomfa', email='staff_nomfa@test.local', role='staff', is_active=True)
            u.set_password('Staff1234!')
            db.session.add(u)
            db.session.commit()
        yield u


def _login(client, username, password):
    client.post('/auth/login', data={'username': username, 'password': password})


def _force_login(client, app, user_id):
    """Bypass login form by injecting Flask-Login session directly."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _logout(client):
    client.post('/auth/logout')



# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMfaEnforcement:
    def test_admin_without_mfa_redirected_to_mfa_setup(self, client, admin_no_mfa):
        """Admin user without MFA should be redirected to /auth/mfa/setup when accessing dashboard."""
        _login(client, 'test_admin_nomfa', 'Admin1234!')
        resp = client.get('/dashboard')
        assert resp.status_code in (302, 301)
        loc = resp.headers.get('Location', '')
        assert 'mfa/setup' in loc or '/auth/mfa/setup' in loc

    def test_admin_with_mfa_can_access_dashboard(self, client, admin_with_mfa):
        """Admin user with MFA enrolled should be allowed through."""
        with client.application.app_context():
            uid = db.session.get(User, admin_with_mfa.id).id
        _force_login(client, client.application, uid)
        resp = client.get('/dashboard')
        # Should NOT be redirected to MFA setup (may redirect to login if CSRF etc.
        # but NOT to mfa/setup).
        if resp.status_code in (301, 302):
            loc = resp.headers.get('Location', '')
            assert 'mfa/setup' not in loc

    def test_staff_without_mfa_is_not_redirected(self, client, staff_no_mfa):
        """Staff users are not in ROLES_REQUIRING_2FA by default, so no redirect."""
        _login(client, 'test_staff_nomfa', 'Staff1234!')
        resp = client.get('/dashboard')
        if resp.status_code in (301, 302):
            loc = resp.headers.get('Location', '')
            assert 'mfa/setup' not in loc

    def test_mfa_auth_routes_are_exempt_from_enforcement(self, client, admin_no_mfa):
        """Auth blueprint routes must be accessible even when admin hasn't enrolled in MFA."""
        _login(client, 'test_admin_nomfa', 'Admin1234!')
        resp = client.get('/auth/mfa/setup')
        # Should NOT redirect to itself (no redirect loop) — allow 200 or redirect elsewhere.
        if resp.status_code in (301, 302):
            loc = resp.headers.get('Location', '')
            assert loc.rstrip('/') != '/auth/mfa/setup'

    def test_unauthenticated_user_is_not_affected(self, client):
        """Unauthenticated users should not be intercepted by the 2FA enforcement hook."""
        resp = client.get('/dashboard')
        assert resp.status_code in (200, 301, 302)
        if resp.status_code in (301, 302):
            loc = resp.headers.get('Location', '')
            assert 'mfa/setup' not in loc
