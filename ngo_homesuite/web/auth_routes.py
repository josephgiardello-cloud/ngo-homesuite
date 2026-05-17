"""
Authentication blueprint for NGO HomeSuite.

Handles user login, registration, logout, and password management.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, abort, current_app
import base64
import json
from authlib.integrations.flask_client import OAuth
from flask_login import login_user, logout_user, login_required, current_user
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError, Length
from datetime import datetime, timezone, timedelta
from flask_wtf import FlaskForm
from sqlalchemy import select
from urllib.parse import urlparse
from ngo_homesuite.models.core import db, User

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# Account lockout policy
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_DURATION_MINUTES = 15


def _is_safe_next_path(next_page: str | None) -> bool:
    if not next_page:
        return False
    value = str(next_page).strip()
    if not value.startswith('/'):
        return False
    # Block scheme-relative and backslash-prefixed values that can trigger external redirects.
    if value.startswith('//') or value.startswith('/\\'):
        return False
    return True


def _same_origin(a: str, b: str) -> bool:
    pa = urlparse(a)
    pb = urlparse(b)
    if not pa.scheme or not pa.netloc or not pb.scheme or not pb.netloc:
        return False
    return (pa.scheme.lower(), pa.netloc.lower()) == (pb.scheme.lower(), pb.netloc.lower())


def _is_same_origin_request() -> bool:
    """Best-effort CSRF guard for logout when global CSRF middleware is not active."""
    host = request.host_url
    origin = (request.headers.get("Origin") or "").strip()
    if origin:
        return _same_origin(origin, host)
    referer = (request.headers.get("Referer") or "").strip()
    if referer:
        return _same_origin(referer, host)
    # Allow clients that don't send Origin/Referer (e.g., CLI/tests/legacy agents).
    return True


class LoginForm(FlaskForm):
    """Form for user login."""
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Sign In')


class RegistrationForm(FlaskForm):
    """Form for user registration."""
    username = StringField('Username', validators=[
        DataRequired(),
        Length(min=3, max=80, message='Username must be between 3 and 80 characters.')
    ])
    email = StringField('Email', validators=[DataRequired(), Email()])
    first_name = StringField('First Name', validators=[Length(max=80)])
    last_name = StringField('Last Name', validators=[Length(max=80)])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters.')
    ])
    password_confirm = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match.')
    ])
    submit = SubmitField('Create Account')
    
    def validate_username(self, field):
        """Check if username already exists."""
        user = db.session.scalars(
            select(User).where(User.username == field.data).limit(1)
        ).first()
        if user:
            raise ValidationError('Username already taken. Please choose a different one.')
    
    def validate_email(self, field):
        """Check if email already exists."""
        user = db.session.scalars(
            select(User).where(User.email == field.data).limit(1)
        ).first()
        if user:
            raise ValidationError('Email already registered. Please use a different one or log in.')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalars(
            select(User).where(User.username == form.username.data).limit(1)
        ).first()

        if user is not None and user.locked_until is not None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if user.locked_until > now:
                remaining = int((user.locked_until - now).total_seconds() // 60) + 1
                flash(
                    f'Account temporarily locked after too many failed attempts. '
                    f'Try again in {remaining} minute(s).',
                    'error',
                )
                return redirect(url_for('auth.login'))

        if user is None or not user.check_password(form.password.data):
            if user is not None:
                user.failed_login_count = (user.failed_login_count or 0) + 1
                if user.failed_login_count >= _MAX_FAILED_ATTEMPTS:
                    user.locked_until = (
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        + timedelta(minutes=_LOCKOUT_DURATION_MINUTES)
                    )
                db.session.commit()
            flash('Invalid username or password.', 'error')
            return redirect(url_for('auth.login'))
        
        if not user.is_active:
            flash('Your account has been deactivated. Please contact support.', 'error')
            return redirect(url_for('auth.login'))
        
        otp_code = (request.form.get('otp_code') or '').strip()
        if bool(user.mfa_enabled):
            if not otp_code:
                flash('Verification code required for this account.', 'error')
                return render_template('auth/login.html', form=form, mfa_required=True)
            if not user.verify_mfa_code(otp_code):
                db.session.commit()
                flash('Invalid verification code.', 'error')
                return render_template('auth/login.html', form=form, mfa_required=True)

        # Successful login — clear lockout counters and rotate session.
        user.failed_login_count = 0
        user.locked_until = None
        db.session.commit()

        # Rotate/clear session before authentication to reduce fixation risk.
        session.clear()
        login_user(user, remember=form.remember_me.data)
        flash(f'Welcome back, {user.first_name or user.username}!', 'success')
        
        # Redirect to next page or dashboard
        next_page = request.args.get('next')
        if _is_safe_next_path(next_page):
            return redirect(next_page)
        return redirect(url_for('main.dashboard'))
    
    return render_template('auth/login.html', form=form)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            role='viewer'  # Default role for new users
        )
        user.set_password(form.password.data)
        
        db.session.add(user)
        db.session.commit()
        
        flash('Account created successfully! You can now log in.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/register.html', form=form)


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """User logout."""
    if not _is_same_origin_request():
        abort(403)
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))


@auth_bp.before_request
def before_request():
    """Update last login timestamp."""
    if current_user.is_authenticated:
        current_user.last_login = datetime.now(timezone.utc)
        db.session.commit()


def _request_data() -> dict:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return request.form.to_dict() if request.form else {}


def _b64url_decode(value: str) -> bytes:
    raw = (value or '').encode('ascii')
    raw += b'=' * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _webauthn_generate_registration_options(*, user: User, rp_id: str, rp_name: str, exclude_ids: list[str]) -> dict:
    from webauthn import generate_registration_options
    from webauthn.helpers import options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor

    exclude_credentials = [
        PublicKeyCredentialDescriptor(id=_b64url_decode(cred_id)) for cred_id in exclude_ids if cred_id
    ]
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=str(user.id).encode('utf-8'),
        user_name=user.username,
        user_display_name=(user.first_name or user.username),
        exclude_credentials=exclude_credentials,
    )
    return json.loads(options_to_json(options))


def _webauthn_verify_registration_response(*, credential: dict, expected_challenge: str, expected_origin: str, expected_rp_id: str) -> dict:
    from webauthn import verify_registration_response

    verification = verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
    )
    return {
        'credential_id': _b64url_encode(verification.credential_id),
        'public_key': _b64url_encode(verification.credential_public_key),
        'sign_count': int(verification.sign_count),
    }


def _webauthn_generate_authentication_options(*, rp_id: str, allow_ids: list[str]) -> dict:
    from webauthn import generate_authentication_options
    from webauthn.helpers import options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor

    allow_credentials = [
        PublicKeyCredentialDescriptor(id=_b64url_decode(cred_id)) for cred_id in allow_ids if cred_id
    ]
    options = generate_authentication_options(rp_id=rp_id, allow_credentials=allow_credentials)
    return json.loads(options_to_json(options))


def _webauthn_verify_authentication_response(
    *,
    credential: dict,
    expected_challenge: str,
    expected_origin: str,
    expected_rp_id: str,
    stored_public_key_b64: str,
    current_sign_count: int,
) -> dict:
    from webauthn import verify_authentication_response

    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        credential_public_key=_b64url_decode(stored_public_key_b64),
        credential_current_sign_count=int(current_sign_count),
    )
    return {'new_sign_count': int(verification.new_sign_count)}


@auth_bp.post('/mfa/enroll')
@login_required
def mfa_enroll():
    """Initialize TOTP secret and backup codes for current user."""
    secret = current_user.ensure_mfa_secret()
    backup_codes = current_user.generate_mfa_backup_codes()
    provisioning_uri = current_user.mfa_provisioning_uri()
    db.session.commit()
    return {
        'secret': secret,
        'provisioning_uri': provisioning_uri,
        'backup_codes': backup_codes,
        'mfa_enabled': bool(current_user.mfa_enabled),
    }, 200


@auth_bp.post('/mfa/confirm')
@login_required
def mfa_confirm():
    """Confirm TOTP setup and enable MFA for current user."""
    data = _request_data()
    code = str(data.get('code') or '').strip()
    if not code:
        return {'error': 'code is required'}, 400
    if not current_user.mfa_totp_secret:
        return {'error': 'mfa enrollment has not been initialized'}, 400
    if not current_user.verify_mfa_code(code):
        db.session.commit()
        return {'error': 'invalid code'}, 400
    current_user.mfa_enabled = True
    db.session.commit()
    return {'status': 'enabled'}, 200


@auth_bp.post('/mfa/disable')
@login_required
def mfa_disable():
    """Disable MFA for current user after code verification."""
    data = _request_data()
    code = str(data.get('code') or '').strip()
    if not current_user.mfa_enabled:
        return {'status': 'already_disabled'}, 200
    if not code:
        return {'error': 'code is required'}, 400
    if not current_user.verify_mfa_code(code):
        db.session.commit()
        return {'error': 'invalid code'}, 400
    current_user.mfa_enabled = False
    current_user.mfa_totp_secret = None
    current_user.mfa_backup_codes_json = []
    db.session.commit()
    return {'status': 'disabled'}, 200


@auth_bp.post('/mfa/backup-codes/rotate')
@login_required
def mfa_rotate_backup_codes():
    """Rotate backup codes for current user.

    Requires current MFA code when MFA is enabled.
    """
    data = _request_data()
    code = str(data.get('code') or '').strip()
    if current_user.mfa_enabled:
        if not code:
            return {'error': 'code is required'}, 400
        if not current_user.verify_mfa_code(code):
            db.session.commit()
            return {'error': 'invalid code'}, 400
    backup_codes = current_user.generate_mfa_backup_codes()
    db.session.commit()
    return {'backup_codes': backup_codes}, 200


@auth_bp.post('/webauthn/register/begin')
@login_required
def webauthn_register_begin():
    """Begin passkey registration for the authenticated user."""
    rp_id = str(current_app.config.get('WEBAUTHN_RP_ID', '')).strip() or str(request.host).split(':', 1)[0]
    rp_name = str(current_app.config.get('WEBAUTHN_RP_NAME', 'NGO HomeSuite')).strip() or 'NGO HomeSuite'
    existing = list(current_user.webauthn_credentials_json or [])
    exclude_ids = [str(item.get('credential_id') or '') for item in existing]
    try:
        options = _webauthn_generate_registration_options(
            user=current_user,
            rp_id=rp_id,
            rp_name=rp_name,
            exclude_ids=exclude_ids,
        )
    except Exception:
        return {'error': 'webauthn registration is unavailable'}, 503

    challenge = str(options.get('challenge') or '')
    if not challenge:
        return {'error': 'could not create registration challenge'}, 500
    session['webauthn_registration_challenge'] = challenge
    session['webauthn_registration_user_id'] = int(current_user.id)
    return {'options': options}, 200


@auth_bp.post('/webauthn/register/complete')
@login_required
def webauthn_register_complete():
    """Complete passkey registration for the authenticated user."""
    expected_challenge = str(session.get('webauthn_registration_challenge') or '')
    expected_user_id = int(session.get('webauthn_registration_user_id') or 0)
    if not expected_challenge or expected_user_id != int(current_user.id):
        return {'error': 'registration session is missing or expired'}, 400

    data = _request_data()
    credential = data.get('credential') if isinstance(data, dict) else None
    if not isinstance(credential, dict):
        return {'error': 'credential is required'}, 400

    try:
        verified = _webauthn_verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_origin=(str(current_app.config.get('WEBAUTHN_ORIGIN', '')).strip() or request.host_url.rstrip('/')),
            expected_rp_id=(str(current_app.config.get('WEBAUTHN_RP_ID', '')).strip() or str(request.host).split(':', 1)[0]),
        )
    except Exception:
        return {'error': 'registration verification failed'}, 400

    credentials = list(current_user.webauthn_credentials_json or [])
    credential_id = str(verified.get('credential_id') or '')
    if not credential_id:
        return {'error': 'registration response missing credential id'}, 400

    if not any(str(item.get('credential_id') or '') == credential_id for item in credentials):
        credentials.append(
            {
                'credential_id': credential_id,
                'public_key': str(verified.get('public_key') or ''),
                'sign_count': int(verified.get('sign_count') or 0),
            }
        )
        current_user.webauthn_credentials_json = credentials
        db.session.commit()

    session.pop('webauthn_registration_challenge', None)
    session.pop('webauthn_registration_user_id', None)
    return {'status': 'registered', 'credential_id': credential_id}, 200


@auth_bp.post('/webauthn/authenticate/begin')
def webauthn_authenticate_begin():
    """Begin passkey authentication for a known user."""
    data = _request_data()
    identifier = str((data or {}).get('identifier') or '').strip().lower()
    if not identifier:
        return {'error': 'identifier is required'}, 400

    user = db.session.scalars(
        select(User).where((User.username == identifier) | (User.email == identifier)).limit(1)
    ).first()
    if user is None or not user.is_active:
        return {'error': 'user not found'}, 404

    credentials = list(user.webauthn_credentials_json or [])
    allow_ids = [str(item.get('credential_id') or '') for item in credentials if item.get('credential_id')]
    if not allow_ids:
        return {'error': 'no registered passkeys'}, 400

    try:
        options = _webauthn_generate_authentication_options(
            rp_id=(str(current_app.config.get('WEBAUTHN_RP_ID', '')).strip() or str(request.host).split(':', 1)[0]),
            allow_ids=allow_ids,
        )
    except Exception:
        return {'error': 'webauthn authentication is unavailable'}, 503

    challenge = str(options.get('challenge') or '')
    if not challenge:
        return {'error': 'could not create authentication challenge'}, 500

    session['webauthn_auth_challenge'] = challenge
    session['webauthn_auth_user_id'] = int(user.id)
    return {'options': options}, 200


@auth_bp.post('/webauthn/authenticate/complete')
def webauthn_authenticate_complete():
    """Complete passkey authentication and create a logged-in session."""
    expected_challenge = str(session.get('webauthn_auth_challenge') or '')
    user_id = int(session.get('webauthn_auth_user_id') or 0)
    if not expected_challenge or not user_id:
        return {'error': 'authentication session is missing or expired'}, 400

    data = _request_data()
    credential = data.get('credential') if isinstance(data, dict) else None
    if not isinstance(credential, dict):
        return {'error': 'credential is required'}, 400

    credential_id = str(credential.get('id') or credential.get('rawId') or '')
    if not credential_id:
        return {'error': 'credential id is required'}, 400

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        return {'error': 'user not found'}, 404

    credentials = list(user.webauthn_credentials_json or [])
    match = next((item for item in credentials if str(item.get('credential_id') or '') == credential_id), None)
    if match is None:
        return {'error': 'unknown passkey'}, 400

    try:
        verified = _webauthn_verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_origin=(str(current_app.config.get('WEBAUTHN_ORIGIN', '')).strip() or request.host_url.rstrip('/')),
            expected_rp_id=(str(current_app.config.get('WEBAUTHN_RP_ID', '')).strip() or str(request.host).split(':', 1)[0]),
            stored_public_key_b64=str(match.get('public_key') or ''),
            current_sign_count=int(match.get('sign_count') or 0),
        )
    except Exception:
        return {'error': 'authentication verification failed'}, 400

    new_sign_count = int(verified.get('new_sign_count') or 0)
    updated_credentials: list[dict] = []
    for item in credentials:
        if str(item.get('credential_id') or '') == credential_id:
            updated_credentials.append(
                {
                    'credential_id': str(item.get('credential_id') or ''),
                    'public_key': str(item.get('public_key') or ''),
                    'sign_count': new_sign_count,
                }
            )
        else:
            updated_credentials.append(item)

    user.webauthn_credentials_json = updated_credentials
    db.session.commit()

    session.pop('webauthn_auth_challenge', None)
    session.pop('webauthn_auth_user_id', None)
    session.clear()
    login_user(user)

    return {'status': 'authenticated', 'redirect': url_for('main.dashboard')}, 200

# ---------------------------------------------------------------------------
# OAuth registry (authlib)
# ---------------------------------------------------------------------------
_oauth = OAuth()

_OAUTH_PROVIDERS: dict[str, dict] = {
    'google': {
        'server_metadata_url': 'https://accounts.google.com/.well-known/openid-configuration',
        'client_kwargs': {'scope': 'openid email profile'},
    },
    'github': {
        'access_token_url': 'https://github.com/login/oauth/access_token',
        'access_token_params': None,
        'authorize_url': 'https://github.com/login/oauth/authorize',
        'authorize_params': None,
        'api_base_url': 'https://api.github.com/',
        'client_kwargs': {'scope': 'user:email read:user'},
    },
}


def _init_oauth(app) -> None:
    """Register OAuth providers using app config."""
    _oauth.init_app(app)
    for name, params in _OAUTH_PROVIDERS.items():
        client_id = app.config.get(f'{name.upper()}_CLIENT_ID', '')
        client_secret = app.config.get(f'{name.upper()}_CLIENT_SECRET', '')
        if client_id and client_secret:
            _oauth.register(name=name, client_id=client_id, client_secret=client_secret, **params)


def _get_oauth_userinfo(provider_name: str, token: dict) -> tuple[str | None, str | None, str | None]:
    """Return (provider_user_id, email, display_name) from an OAuth token/userinfo response.

    Returns (None, None, None) when the provider response cannot be parsed.
    """
    client = _oauth.create_client(provider_name)
    if provider_name == 'google':
        userinfo = token.get('userinfo') or client.userinfo()
        uid = str(userinfo.get('sub') or '')
        email = str(userinfo.get('email') or '')
        name = userinfo.get('name') or ''
    elif provider_name == 'github':
        resp = client.get('user', token=token)
        userinfo = resp.json()
        uid = str(userinfo.get('id') or '')
        # GitHub may not expose email in /user; fall back to /user/emails
        email = str(userinfo.get('email') or '')
        if not email:
            emails_resp = client.get('user/emails', token=token)
            for entry in emails_resp.json():
                if entry.get('primary') and entry.get('verified'):
                    email = str(entry.get('email') or '')
                    break
        name = userinfo.get('name') or userinfo.get('login') or ''
    else:
        return None, None, None
    return (uid or None, email or None, name or '')


@auth_bp.get('/oauth/<provider>')
def oauth_login(provider: str):
    """Redirect to OAuth provider's authorization page."""
    if provider not in _OAUTH_PROVIDERS:
        flash('Unknown OAuth provider.', 'error')
        return redirect(url_for('auth.login'))

    client = _oauth.create_client(provider)
    if client is None:
        flash(f'{provider.title()} login is not configured on this server.', 'error')
        return redirect(url_for('auth.login'))

    redirect_uri = url_for('auth.oauth_callback', provider=provider, _external=True)
    return client.authorize_redirect(redirect_uri)


@auth_bp.get('/oauth/<provider>/callback')
def oauth_callback(provider: str):
    """Handle OAuth provider callback, resolve user, and log them in."""
    if provider not in _OAUTH_PROVIDERS:
        flash('Unknown OAuth provider.', 'error')
        return redirect(url_for('auth.login'))

    client = _oauth.create_client(provider)
    if client is None:
        flash(f'{provider.title()} login is not configured on this server.', 'error')
        return redirect(url_for('auth.login'))

    try:
        token = client.authorize_access_token()
    except Exception:
        flash('OAuth authorization failed. Please try again.', 'error')
        return redirect(url_for('auth.login'))

    provider_uid, email, display_name = _get_oauth_userinfo(provider, token)
    if not provider_uid or not email:
        flash('Could not retrieve account information from the provider.', 'error')
        return redirect(url_for('auth.login'))

    # 1. Look for existing user linked to this provider identity.
    user = db.session.scalars(
        select(User).where(
            User.oauth_provider == provider,
            User.oauth_provider_id == provider_uid,
        ).limit(1)
    ).first()

    if user is None:
        # 2. Try to link by email (existing password account).
        user = db.session.scalars(
            select(User).where(User.email == email).limit(1)
        ).first()
        if user is not None:
            user.oauth_provider = provider
            user.oauth_provider_id = provider_uid
            db.session.commit()

    if user is None:
        # 3. Auto-create a new account from the OAuth identity.
        parts = (display_name or '').split(' ', 1)
        first = parts[0] if parts else ''
        last = parts[1] if len(parts) > 1 else ''
        base_username = (email.split('@')[0] or provider)[:75].replace(' ', '_')
        # Ensure username uniqueness
        username = base_username
        suffix = 1
        while db.session.scalars(
            select(User).where(User.username == username).limit(1)
        ).first() is not None:
            username = f'{base_username}{suffix}'
            suffix += 1

        user = User(
            username=username,
            email=email,
            first_name=first,
            last_name=last,
            password_hash='!oauth',   # sentinel — not a valid argon2 hash
            role='viewer',
            oauth_provider=provider,
            oauth_provider_id=provider_uid,
        )
        db.session.add(user)
        db.session.commit()

    if not user.is_active:
        flash('Your account has been deactivated. Please contact support.', 'error')
        return redirect(url_for('auth.login'))

    # Clear session before logging in (session fixation prevention).
    session.clear()
    login_user(user)
    flash(f'Welcome, {user.first_name or user.username}!', 'success')

    next_page = request.args.get('next')
    if _is_safe_next_path(next_page):
        return redirect(next_page)
    return redirect(url_for('main.dashboard'))
