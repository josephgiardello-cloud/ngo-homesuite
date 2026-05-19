"""Tests for A-3: Step-up authentication."""
from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import User, db


@pytest.fixture(scope='module')
def app():
    return create_app(TestingConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def mfa_user(app):
    with app.app_context():
        u = User.query.filter_by(username='test_stepup_user').first()
        if u is None:
            u = User(username='test_stepup_user', email='stepup@test.local', role='staff', is_active=True)
            u.set_password('Step1234!')
            db.session.add(u)
            db.session.commit()
        u.ensure_mfa_secret()
        u.mfa_enabled = True
        db.session.commit()
        yield u


@pytest.fixture()
def no_mfa_user(app):
    with app.app_context():
        u = User.query.filter_by(username='test_stepup_nomfa').first()
        if u is None:
            u = User(username='test_stepup_nomfa', email='stepup_nomfa@test.local', role='staff', is_active=True)
            u.set_password('Step1234!')
            db.session.add(u)
            db.session.commit()
        u.mfa_enabled = False
        db.session.commit()
        yield u


def _force_login(client, app, user_id):
    """Bypass the login form by directly injecting the Flask-Login session cookie."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


class TestStepUpOtp:
    def test_step_up_requires_login(self, client):
        resp = client.post('/auth/step-up-otp', json={'code': '123456'})
        # Should redirect to login or return 401.
        assert resp.status_code in (302, 401)

    def test_step_up_rejects_invalid_code(self, client, mfa_user, app):
        with app.app_context():
            user = db.session.get(User, mfa_user.id)
            uid = user.id
        _force_login(client, app, uid)
        resp = client.post('/auth/step-up-otp', json={'code': '000000'})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data is not None
        assert 'invalid' in data.get('error', '').lower()

    def test_step_up_rejects_missing_code(self, client, mfa_user, app):
        with app.app_context():
            uid = db.session.get(User, mfa_user.id).id
        _force_login(client, app, uid)
        resp = client.post('/auth/step-up-otp', json={})
        assert resp.status_code == 400

    def test_step_up_rejects_user_without_mfa(self, client, no_mfa_user, app):
        with app.app_context():
            uid = db.session.get(User, no_mfa_user.id).id
        _force_login(client, app, uid)
        resp = client.post('/auth/step-up-otp', json={'code': '123456'})
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'not enabled' in (data or {}).get('error', '').lower()

    def test_step_up_rate_limited_returns_429(self, client, mfa_user, app, monkeypatch):
        with app.app_context():
            uid = db.session.get(User, mfa_user.id).id
        _force_login(client, app, uid)
        monkeypatch.setattr('ngo_homesuite.web.auth_routes._auth_rate_limited', lambda *args, **kwargs: True)
        resp = client.post('/auth/step-up-otp', json={'code': '123456'})
        assert resp.status_code == 429
        data = resp.get_json() or {}
        assert 'too many' in str(data.get('error') or '').lower()

    def test_step_up_succeeds_with_valid_code(self, client, mfa_user, app):
        """Test that step-up succeeds when provided a valid TOTP code."""
        import pyotp
        with app.app_context():
            user = db.session.get(User, mfa_user.id)
            uid = user.id
            secret = user.ensure_mfa_secret()
        _force_login(client, app, uid)
        totp = pyotp.TOTP(secret)
        code = totp.now()
        resp = client.post('/auth/step-up-otp', json={'code': code})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'verified'
        assert 'expires_in_seconds' in data


class TestRequireStepUpAuthDecorator:
    """Test that the require_step_up_auth decorator blocks unverified requests."""

    def test_decorator_returns_403_when_step_up_not_verified(self):
        """Unit-test: decorator returns 403 JSON with step_up_required=True."""
        from unittest.mock import MagicMock, patch

        from ngo_homesuite.web.auth_routes import require_step_up_auth

        @require_step_up_auth
        def protected_view():
            return 'ok', 200

        with patch('ngo_homesuite.web.auth_routes.is_step_up_verified', return_value=False), \
             patch('ngo_homesuite.web.auth_routes.current_user') as mock_user, \
               patch('ngo_homesuite.audit.security_events.SecurityAuditService.log_event'):
            mock_user.is_authenticated = True
            mock_user.id = 1
            from flask import Flask
            app = Flask(__name__)
            with app.test_request_context('/test/protected'):
                result = protected_view()
                assert result[1] == 403
                data = result[0].get_json()
                assert data['step_up_required'] is True
