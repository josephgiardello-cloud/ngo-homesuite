from __future__ import annotations
# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingParameterType=false, reportCallIssue=false

import pytest
import pyotp
import unittest.mock as mock

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.auth.identity import NormalizedIdentity
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
    csp = rv.headers.get("Content-Security-Policy", "")
    assert "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com" in csp
    assert "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com" in csp


# ---------------------------------------------------------------------------
# Logout security
# ---------------------------------------------------------------------------

def test_logout_clears_session(client, app):
    _ensure_user(app, "auth_logout_user", "AuthPass123!")

    # Log in
    client.post(
        "/auth/login",
        data={"username": "auth_logout_user", "password": "AuthPass123!"},
        follow_redirects=False,
    )
    with client.session_transaction() as sess:
        assert sess.get("_user_id") is not None

    # Log out
    rv = client.post("/auth/logout", follow_redirects=False)
    assert rv.status_code == 302

    with client.session_transaction() as sess:
        assert sess.get("_user_id") is None


def test_logout_redirects_to_index(client, app):
    _ensure_user(app, "auth_logout_user2", "AuthPass123!")
    client.post(
        "/auth/login",
        data={"username": "auth_logout_user2", "password": "AuthPass123!"},
        follow_redirects=False,
    )
    rv = client.post("/auth/logout", follow_redirects=False)
    assert rv.status_code == 302
    location = rv.headers.get("Location", "")
    assert location.endswith("/") or "/index" in location or location == "/"


def test_logout_get_is_not_allowed(client, app):
    _ensure_user(app, "auth_logout_user3", "AuthPass123!")
    client.post(
        "/auth/login",
        data={"username": "auth_logout_user3", "password": "AuthPass123!"},
        follow_redirects=False,
    )
    rv = client.get("/auth/logout", follow_redirects=False)
    assert rv.status_code == 405


def test_logout_rejects_cross_site_origin_and_keeps_session(client, app):
    _ensure_user(app, "auth_logout_user4", "AuthPass123!")
    client.post(
        "/auth/login",
        data={"username": "auth_logout_user4", "password": "AuthPass123!"},
        follow_redirects=False,
    )

    rv = client.post(
        "/auth/logout",
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert rv.status_code == 403

    with client.session_transaction() as sess:
        assert sess.get("_user_id") is not None


def test_login_response_sets_cookie_security_attributes(client, app):
    _ensure_user(app, "auth_cookie_user", "AuthPass123!")

    rv = client.post(
        "/auth/login",
        data={"username": "auth_cookie_user", "password": "AuthPass123!", "remember_me": "y"},
        follow_redirects=False,
    )
    assert rv.status_code == 302

    set_cookie_headers = rv.headers.getlist("Set-Cookie")
    assert set_cookie_headers, "Expected Set-Cookie headers on login"

    session_cookie = next((h for h in set_cookie_headers if h.startswith("session=")), "")
    assert session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=" in session_cookie

    remember_cookie = next((h for h in set_cookie_headers if h.startswith("remember_token=")), "")
    assert remember_cookie
    assert "HttpOnly" in remember_cookie
    assert "SameSite=" in remember_cookie


# ---------------------------------------------------------------------------
# Registration validation
# ---------------------------------------------------------------------------

def test_register_happy_path_redirects_to_login(client, app):
    rv = client.post(
        "/auth/register",
        data={
            "username": "new_valid_user",
            "email": "new_valid_user@example.com",
            "password": "StrongPass1!",
            "password_confirm": "StrongPass1!",
        },
        follow_redirects=False,
    )
    # Should redirect to login after successful registration
    assert rv.status_code == 302
    assert "/auth/login" in (rv.headers.get("Location") or "")


def test_register_rejects_duplicate_username(client, app):
    _ensure_user(app, "dup_username_user", "AuthPass123!")

    rv = client.post(
        "/auth/register",
        data={
            "username": "dup_username_user",
            "email": "dup_unique@test.local",
            "password": "StrongPass1!",
            "password_confirm": "StrongPass1!",
        },
        follow_redirects=False,
    )
    # Form re-renders with validation error (200), does not redirect
    assert rv.status_code == 200
    assert b"already taken" in rv.data or b"Username" in rv.data


def test_register_rejects_mismatched_passwords(client, app):
    rv = client.post(
        "/auth/register",
        data={
            "username": "mismatch_pw_user",
            "email": "mismatch_pw@test.local",
            "password": "StrongPass1!",
            "password_confirm": "DifferentPass2!",
        },
        follow_redirects=False,
    )
    assert rv.status_code == 200
    assert b"match" in rv.data.lower() or b"password" in rv.data.lower()


def test_register_rejects_short_password(client, app):
    rv = client.post(
        "/auth/register",
        data={
            "username": "short_pw_user",
            "email": "short_pw@test.local",
            "password": "short",
            "password_confirm": "short",
        },
        follow_redirects=False,
    )
    assert rv.status_code == 200
    assert b"8" in rv.data or b"characters" in rv.data.lower() or b"password" in rv.data.lower()


def test_mfa_enroll_confirm_and_login_requires_otp(client, app):
    _ensure_user(app, "mfa_user1", "AuthPass123!")

    # Log in to initialize enrollment.
    rv_login = client.post(
        "/auth/login",
        data={"username": "mfa_user1", "password": "AuthPass123!"},
        follow_redirects=False,
    )
    assert rv_login.status_code == 302

    # Enroll MFA and retrieve TOTP secret.
    rv_enroll = client.post("/auth/mfa/enroll", json={})
    assert rv_enroll.status_code == 200
    body = rv_enroll.get_json() or {}
    secret = body.get("secret")
    assert isinstance(secret, str) and secret
    assert isinstance(body.get("backup_codes"), list) and len(body["backup_codes"]) >= 5

    # Confirm MFA with valid TOTP code.
    code = pyotp.TOTP(secret).now()
    rv_confirm = client.post("/auth/mfa/confirm", json={"code": code})
    assert rv_confirm.status_code == 200
    assert (rv_confirm.get_json() or {}).get("status") == "enabled"

    # Log out and ensure next login requires OTP.
    client.post("/auth/logout", follow_redirects=False)
    rv_no_otp = client.post(
        "/auth/login",
        data={"username": "mfa_user1", "password": "AuthPass123!"},
        follow_redirects=False,
    )
    assert rv_no_otp.status_code == 200
    assert b"verification code" in rv_no_otp.data.lower()

    rv_with_otp = client.post(
        "/auth/login",
        data={
            "username": "mfa_user1",
            "password": "AuthPass123!",
            "otp_code": pyotp.TOTP(secret).now(),
        },
        follow_redirects=False,
    )
    assert rv_with_otp.status_code == 302
    assert "/dashboard" in (rv_with_otp.headers.get("Location") or "")


def test_mfa_backup_code_can_authenticate_once(client, app):
    _ensure_user(app, "mfa_user2", "AuthPass123!")

    rv_login = client.post(
        "/auth/login",
        data={"username": "mfa_user2", "password": "AuthPass123!"},
        follow_redirects=False,
    )
    assert rv_login.status_code == 302

    rv_enroll = client.post("/auth/mfa/enroll", json={})
    assert rv_enroll.status_code == 200
    payload = rv_enroll.get_json() or {}
    secret = payload["secret"]
    backup = payload["backup_codes"][0]

    rv_confirm = client.post("/auth/mfa/confirm", json={"code": pyotp.TOTP(secret).now()})
    assert rv_confirm.status_code == 200

    client.post("/auth/logout", follow_redirects=False)

    # Backup code works once.
    rv_backup_ok = client.post(
        "/auth/login",
        data={"username": "mfa_user2", "password": "AuthPass123!", "otp_code": backup},
        follow_redirects=False,
    )
    assert rv_backup_ok.status_code == 302

    client.post("/auth/logout", follow_redirects=False)

    # Reuse of the same backup code should fail.
    rv_backup_reuse = client.post(
        "/auth/login",
        data={"username": "mfa_user2", "password": "AuthPass123!", "otp_code": backup},
        follow_redirects=False,
    )
    assert rv_backup_reuse.status_code == 200
    assert b"invalid verification code" in rv_backup_reuse.data.lower()


# ---------------------------------------------------------------------------
# OAuth / SSO login
# ---------------------------------------------------------------------------

def _make_oauth_app():
    """App with fake Google + GitHub client IDs so OAuth routes are registered."""
    class _OAuthTestConfig(TestingConfig):
        GOOGLE_CLIENT_ID = 'fake-google-id'
        GOOGLE_CLIENT_SECRET = 'fake-google-secret'
        GITHUB_CLIENT_ID = 'fake-github-id'
        GITHUB_CLIENT_SECRET = 'fake-github-secret'
    return create_app(_OAuthTestConfig)


@pytest.fixture(scope='module')
def oauth_app():
    return _make_oauth_app()


@pytest.fixture()
def oauth_client(oauth_app):
    return oauth_app.test_client()


def _stub_token_exchange(provider, token, userinfo):
    """Return a context manager that patches authorize_access_token and userinfo fetch."""
    import ngo_homesuite.web.auth_routes as ar

    orig_create = ar._oauth.create_client

    def _create_client(name):
        if name != provider:
            return orig_create(name)
        client = mock.MagicMock()
        client.authorize_access_token.return_value = token
        client.userinfo.return_value = userinfo          # Google path
        # GitHub path: client.get('user') and client.get('user/emails')
        user_resp = mock.MagicMock()
        user_resp.json.return_value = userinfo
        client.get.return_value = user_resp
        return client

    return mock.patch.object(ar._oauth, 'create_client', side_effect=_create_client)


def test_oauth_google_creates_new_user_and_logs_in(oauth_client, oauth_app):
    """A first-time Google OAuth login should create a new user and redirect to dashboard."""
    token = {'access_token': 'tok', 'token_type': 'Bearer',
             'userinfo': {'sub': 'google-uid-001', 'email': 'oauth_google@example.com', 'name': 'Oauth User'}}

    with _stub_token_exchange('google', token, token['userinfo']):
        rv = oauth_client.get('/auth/oauth/google/callback', follow_redirects=False)

    assert rv.status_code == 302
    assert '/dashboard' in (rv.headers.get('Location') or '')

    with oauth_app.app_context():
        user = User.query.filter_by(email='oauth_google@example.com').first()
        assert user is not None
        assert user.oauth_provider == 'google'
        assert user.oauth_provider_id == 'google-uid-001'
        assert user.password_hash == '!oauth'


def test_oauth_google_links_existing_email_account(oauth_client, oauth_app):
    """OAuth login with a matching email should link the provider to the existing account."""
    with oauth_app.app_context():
        existing = User(
            username='preexisting_email_user',
            email='preexisting@example.com',
            role='viewer',
        )
        existing.set_password('SomePass1!')
        db.session.add(existing)
        db.session.commit()
        existing_id = existing.id

    token = {'access_token': 'tok', 'token_type': 'Bearer',
             'userinfo': {'sub': 'google-uid-link', 'email': 'preexisting@example.com', 'name': 'Pre Existing'}}

    with _stub_token_exchange('google', token, token['userinfo']):
        rv = oauth_client.get('/auth/oauth/google/callback', follow_redirects=False)

    assert rv.status_code == 302
    with oauth_app.app_context():
        user = db.session.get(User, existing_id)
        assert user is not None
        assert user.oauth_provider == 'google'
        assert user.oauth_provider_id == 'google-uid-link'
        # Password should remain intact (it was an existing password account)
        assert user.password_hash != '!oauth'


def test_oauth_google_links_existing_account_case_insensitive_email(oauth_client, oauth_app):
    with oauth_app.app_context():
        existing = User(
            username='preexisting_case_email_user',
            email='CaseSensitive@example.com',
            role='viewer',
        )
        existing.set_password('SomePass1!')
        db.session.add(existing)
        db.session.commit()
        existing_id = existing.id

    token = {
        'access_token': 'tok',
        'token_type': 'Bearer',
        'userinfo': {'sub': 'google-uid-case-link', 'email': 'casesensitive@example.com', 'name': 'Case User'},
    }

    with _stub_token_exchange('google', token, token['userinfo']):
        rv = oauth_client.get('/auth/oauth/google/callback', follow_redirects=False)

    assert rv.status_code == 302
    with oauth_app.app_context():
        user = db.session.get(User, existing_id)
        assert user is not None
        assert user.oauth_provider == 'google'
        assert user.oauth_provider_id == 'google-uid-case-link'


def test_normalized_identity_from_oauth_normalizes_provider_and_email():
    identity = NormalizedIdentity.from_oauth(
        provider='Google',
        provider_user_id='abc-123',
        email='User.Name@Example.ORG ',
        display_name=' User Name ',
    )

    assert identity.provider == 'google'
    assert identity.provider_user_id == 'abc-123'
    assert identity.email == 'user.name@example.org'
    assert identity.email_normalized == 'user.name@example.org'
    assert identity.display_name == 'User Name'


def test_oauth_repeated_login_reuses_account(oauth_client, oauth_app):
    """Second OAuth login with the same provider ID should reuse the existing user."""
    token = {'access_token': 'tok', 'token_type': 'Bearer',
             'userinfo': {'sub': 'google-uid-repeat', 'email': 'oauth_repeat@example.com', 'name': 'Repeat User'}}

    with _stub_token_exchange('google', token, token['userinfo']):
        oauth_client.get('/auth/oauth/google/callback', follow_redirects=False)

    # Second login — same provider UID
    with _stub_token_exchange('google', token, token['userinfo']):
        rv2 = oauth_client.get('/auth/oauth/google/callback', follow_redirects=False)

    assert rv2.status_code == 302
    with oauth_app.app_context():
        count = User.query.filter_by(email='oauth_repeat@example.com').count()
        assert count == 1, 'Should not duplicate user on repeated OAuth login'


def test_oauth_github_creates_new_user(oauth_client, oauth_app):
    """GitHub OAuth login creates a new user from GitHub profile data."""
    gh_profile = {'id': 999001, 'email': 'oauth_github@example.com', 'name': 'GH User', 'login': 'gh_user_001'}
    token = {'access_token': 'gh-tok', 'token_type': 'bearer'}

    with _stub_token_exchange('github', token, gh_profile):
        rv = oauth_client.get('/auth/oauth/github/callback', follow_redirects=False)

    assert rv.status_code == 302
    assert '/dashboard' in (rv.headers.get('Location') or '')
    with oauth_app.app_context():
        user = User.query.filter_by(email='oauth_github@example.com').first()
        assert user is not None
        assert user.oauth_provider == 'github'
        assert user.oauth_provider_id == '999001'


def test_oauth_unknown_provider_redirects_to_login(oauth_client, oauth_app):
    """Requesting an unknown OAuth provider should redirect back to login with an error."""
    rv = oauth_client.get('/auth/oauth/unknown_provider', follow_redirects=False)
    assert rv.status_code == 302
    assert '/auth/login' in (rv.headers.get('Location') or '')


def test_oauth_callback_failed_token_exchange_redirects_to_login(oauth_client, oauth_app):
    """If token exchange raises, redirect to login with error flash."""
    import ngo_homesuite.web.auth_routes as ar

    def _bad_client(name):
        client = mock.MagicMock()
        client.authorize_access_token.side_effect = Exception('state mismatch')
        return client

    with mock.patch.object(ar._oauth, 'create_client', side_effect=_bad_client):
        rv = oauth_client.get('/auth/oauth/google/callback', follow_redirects=False)

    assert rv.status_code == 302
    assert '/auth/login' in (rv.headers.get('Location') or '')


def test_login_template_hides_oauth_buttons_by_default(oauth_client, oauth_app):
    """Login page should hide SSO link targets when hide toggle is enabled."""
    rv = oauth_client.get('/auth/login')
    assert rv.status_code == 200
    body = rv.data
    assert b'/auth/oauth/google' not in body
    assert b'/auth/oauth/github' not in body


def test_login_template_can_show_oauth_buttons_when_enabled():
    """SSO link targets should render when hide toggle is explicitly disabled."""
    class _VisibleOAuthConfig(TestingConfig):
        GOOGLE_CLIENT_ID = 'fake-google-id'
        GOOGLE_CLIENT_SECRET = 'fake-google-secret'
        GITHUB_CLIENT_ID = 'fake-github-id'
        GITHUB_CLIENT_SECRET = 'fake-github-secret'
        HIDE_SSO_OPTIONS = False

    app = create_app(_VisibleOAuthConfig)
    client = app.test_client()
    rv = client.get('/auth/login')
    assert rv.status_code == 200
    body = rv.data
    assert b'/auth/oauth/google' in body
    assert b'/auth/oauth/github' in body


# ---------------------------------------------------------------------------
# WebAuthn / Passkeys
# ---------------------------------------------------------------------------

def test_webauthn_register_begin_and_complete_stores_credential(client, app):
    _ensure_user(app, "passkey_user1", "AuthPass123!")

    rv_login = client.post(
        "/auth/login",
        data={"username": "passkey_user1", "password": "AuthPass123!"},
        follow_redirects=False,
    )
    assert rv_login.status_code == 302

    import ngo_homesuite.web.auth_routes as ar

    with mock.patch.object(
        ar,
        '_webauthn_generate_registration_options',
        return_value={"challenge": "reg-challenge-1", "rp": {"id": "localhost"}},
    ):
        rv_begin = client.post('/auth/webauthn/register/begin', json={})

    assert rv_begin.status_code == 200
    body_begin = rv_begin.get_json() or {}
    assert (body_begin.get('options') or {}).get('challenge') == 'reg-challenge-1'

    with mock.patch.object(
        ar,
        '_webauthn_verify_registration_response',
        return_value={
            'credential_id': 'cred-reg-001',
            'public_key': 'pk-reg-001',
            'sign_count': 0,
        },
    ):
        rv_complete = client.post('/auth/webauthn/register/complete', json={'credential': {'id': 'cred-reg-001'}})

    assert rv_complete.status_code == 200
    body_complete = rv_complete.get_json() or {}
    assert body_complete.get('status') == 'registered'
    assert body_complete.get('credential_id') == 'cred-reg-001'

    with app.app_context():
        user = User.query.filter_by(username='passkey_user1').first()
        assert user is not None
        creds = list(user.webauthn_credentials_json or [])
        assert len(creds) == 1
        assert creds[0].get('credential_id') == 'cred-reg-001'


def test_webauthn_authenticate_begin_and_complete_logs_in(client, app):
    _ensure_user(app, "passkey_user2", "AuthPass123!")

    with app.app_context():
        user = User.query.filter_by(username='passkey_user2').first()
        assert user is not None
        user.webauthn_credentials_json = [
            {'credential_id': 'cred-auth-001', 'public_key': 'pk-auth-001', 'sign_count': 1}
        ]
        db.session.commit()

    import ngo_homesuite.web.auth_routes as ar

    with mock.patch.object(
        ar,
        '_webauthn_generate_authentication_options',
        return_value={"challenge": "auth-challenge-1", "allowCredentials": [{"id": "cred-auth-001"}]},
    ):
        rv_begin = client.post('/auth/webauthn/authenticate/begin', json={'identifier': 'passkey_user2'})

    assert rv_begin.status_code == 200
    body_begin = rv_begin.get_json() or {}
    assert (body_begin.get('options') or {}).get('challenge') == 'auth-challenge-1'

    with mock.patch.object(
        ar,
        '_webauthn_verify_authentication_response',
        return_value={'new_sign_count': 2},
    ):
        rv_complete = client.post('/auth/webauthn/authenticate/complete', json={'credential': {'id': 'cred-auth-001'}})

    assert rv_complete.status_code == 200
    body_complete = rv_complete.get_json() or {}
    assert body_complete.get('status') == 'authenticated'
    assert '/dashboard' in (body_complete.get('redirect') or '')

    with client.session_transaction() as sess:
        assert sess.get('_user_id') is not None

    with app.app_context():
        user = User.query.filter_by(username='passkey_user2').first()
        assert user is not None
        creds = list(user.webauthn_credentials_json or [])
        assert creds[0].get('sign_count') == 2


def test_webauthn_authenticate_complete_rejects_unknown_credential(client, app):
    _ensure_user(app, "passkey_user3", "AuthPass123!")
    with app.app_context():
        user = User.query.filter_by(username='passkey_user3').first()
        assert user is not None
        user.webauthn_credentials_json = [
            {'credential_id': 'known-cred-001', 'public_key': 'pk-known-001', 'sign_count': 0}
        ]
        db.session.commit()

    import ngo_homesuite.web.auth_routes as ar

    with mock.patch.object(
        ar,
        '_webauthn_generate_authentication_options',
        return_value={"challenge": "auth-challenge-2", "allowCredentials": [{"id": "known-cred-001"}]},
    ):
        rv_begin = client.post('/auth/webauthn/authenticate/begin', json={'identifier': 'passkey_user3'})
    assert rv_begin.status_code == 200

    rv_complete = client.post('/auth/webauthn/authenticate/complete', json={'credential': {'id': 'unknown-cred-999'}})
    assert rv_complete.status_code == 400
    assert (rv_complete.get_json() or {}).get('error') == 'unknown passkey'
