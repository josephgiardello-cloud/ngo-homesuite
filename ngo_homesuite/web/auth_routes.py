"""
Authentication blueprint for NGO HomeSuite.

Handles user login, registration, logout, and password management.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, abort, current_app
import base64
import hashlib
import json
import os
from authlib.integrations.flask_client import OAuth
from flask_login import login_user, logout_user, login_required, current_user
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError, Length
from datetime import datetime, timezone, timedelta
from flask_wtf import FlaskForm
from itsdangerous import URLSafeTimedSerializer, BadSignature, BadTimeSignature, SignatureExpired
from sqlalchemy import select, or_, func
from urllib.parse import urlparse
from urllib.parse import quote_plus
from collections import defaultdict, deque
from threading import Lock
from ngo_homesuite.models.core import db, User
from ngo_homesuite.auth.identity import NormalizedIdentity

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# Account lockout policy
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_DURATION_MINUTES = 15
_MAX_MFA_FAILED_ATTEMPTS = 5
_MFA_LOCKOUT_DURATION_MINUTES = 15
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_RATE_LIMIT = 10
_FORGOT_WINDOW_SECONDS = 60
_FORGOT_RATE_LIMIT = 5

_LOGIN_ATTEMPT_BUCKETS: dict[str, deque[datetime]] = defaultdict(deque)
_FORGOT_ATTEMPT_BUCKETS: dict[str, deque[datetime]] = defaultdict(deque)
_RATE_LIMIT_LOCK = Lock()


def _dev_login_credentials() -> dict[str, str] | None:
    if not bool(current_app.config.get('SHOW_DEV_LOGIN_CREDENTIALS', False)):
        return None
    if not (current_app.config.get('DEBUG') or current_app.config.get('ENABLE_DEMO_SEED')):
        return None
    return {
        'admin_username': 'admin',
        'admin_password': current_app.config.get('DEMO_ADMIN_PASSWORD', 'admin123!'),
        'staff_username': 'staff',
        'staff_password': 'staff123!',
        'viewer_username': 'viewer',
        'viewer_password': 'viewer123!',
    }


def _mask_config_value(value: str, *, keep_start: int = 3, keep_end: int = 2) -> str:
    raw = str(value or '').strip()
    if not raw:
        return '(missing)'
    if len(raw) <= (keep_start + keep_end):
        return '*' * len(raw)
    return f"{raw[:keep_start]}{'*' * (len(raw) - keep_start - keep_end)}{raw[-keep_end:]}"


def _auth_rate_limited(
    buckets: dict[str, deque[datetime]],
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> bool:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with _RATE_LIMIT_LOCK:
        queue = buckets[key]
        cutoff = now - timedelta(seconds=max(1, int(window_seconds)))
        while queue and queue[0] < cutoff:
            queue.popleft()
        if len(queue) >= max(1, int(limit)):
            return True
        queue.append(now)
        return False


def _provider_metadata_url(provider_name: str, provider_params: dict, app=None) -> str:
    config_source = app.config if app is not None else current_app.config
    if provider_name == 'okta':
        return str(config_source.get('OKTA_SERVER_METADATA_URL', '')).strip()
    return str(provider_params.get('server_metadata_url') or '').strip()


def _oauth_provider_diagnostics(app=None) -> dict[str, dict]:
    config_source = app.config if app is not None else current_app.config
    diagnostics: dict[str, dict] = {}
    for name, params in _OAUTH_PROVIDERS.items():
        client_id = str(config_source.get(f'{name.upper()}_CLIENT_ID', '')).strip()
        client_secret = str(config_source.get(f'{name.upper()}_CLIENT_SECRET', '')).strip()
        metadata_url = _provider_metadata_url(name, params, app=app)

        reasons: list[str] = []
        if not client_id:
            reasons.append('missing_client_id')
        if not client_secret:
            reasons.append('missing_client_secret')
        if name == 'okta' and not metadata_url:
            reasons.append('missing_okta_metadata_url')

        registered = False
        if not reasons:
            try:
                registered = _oauth.create_client(name) is not None
            except Exception:
                registered = False
                reasons.append('client_registry_error')

        diagnostics[name] = {
            'configured': not reasons,
            'registered': registered,
            'client_id_masked': _mask_config_value(client_id),
            'client_secret_masked': _mask_config_value(client_secret),
            'metadata_url': metadata_url,
            'reasons': reasons,
            'authorize_path': f"/auth/oauth/{name}",
            'callback_path': f"/auth/oauth/{name}/callback",
        }
    return diagnostics


def _oauth_provider_enabled(provider: str) -> bool:
    """Return True when an OAuth provider client is registered and ready."""
    try:
        return _oauth.create_client(provider) is not None
    except Exception:
        return False


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
    username = StringField('Username or Email', validators=[DataRequired(), Length(min=3, max=120)])
    password = PasswordField('Password', validators=[DataRequired()])
    otp_code = StringField('Verification Code')
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Sign In')


class ForgotPasswordForm(FlaskForm):
    """Request a password reset link."""
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Send Reset Link')


class ResetPasswordForm(FlaskForm):
    """Set a new password using a reset token."""
    password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters.'),
    ])
    password_confirm = PasswordField('Confirm New Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match.'),
    ])
    submit = SubmitField('Reset Password')


def _password_reset_serializer() -> URLSafeTimedSerializer:
    secret_key = str(current_app.config.get('SECRET_KEY') or '')
    return URLSafeTimedSerializer(secret_key=secret_key, salt='password-reset-v1')


def _issue_password_reset_token(user: User) -> str:
    serializer = _password_reset_serializer()
    pwd_sig = hashlib.sha256(str(user.password_hash or '').encode('utf-8')).hexdigest()[:20]
    payload = {
        'uid': int(user.id),
        'email': str(user.email or '').strip().lower(),
        'pwd': pwd_sig,
    }
    return serializer.dumps(payload)


def _resolve_user_from_password_reset_token(token: str) -> User | None:
    if not token:
        return None
    max_age = int(current_app.config.get('PASSWORD_RESET_TOKEN_TTL_SECONDS', 3600))
    serializer = _password_reset_serializer()
    try:
        payload = serializer.loads(token, max_age=max_age)
    except (BadSignature, BadTimeSignature, SignatureExpired):
        return None

    user_id = payload.get('uid')
    email = str(payload.get('email') or '').strip().lower()
    pwd_sig = str(payload.get('pwd') or '').strip().lower()
    if not user_id or not email or not pwd_sig:
        return None

    user = db.session.get(User, int(user_id))
    if user is None or not bool(user.is_active):
        return None
    if str(user.email or '').strip().lower() != email:
        return None

    current_sig = hashlib.sha256(str(user.password_hash or '').encode('utf-8')).hexdigest()[:20].lower()
    if current_sig != pwd_sig:
        return None
    return user


def _dispatch_password_reset_email(email: str, reset_url: str) -> None:
    """Best-effort local delivery: logs reset URL when SMTP is unavailable."""
    email = str(email or '').strip().lower()
    if not email:
        return

    from email.message import EmailMessage
    import smtplib

    smtp_host = str(current_app.config.get('MAIL_SERVER') or '').strip()
    smtp_port = int(current_app.config.get('MAIL_PORT') or 587)
    smtp_user = str(current_app.config.get('MAIL_USERNAME') or '').strip()
    smtp_pass = str(current_app.config.get('MAIL_PASSWORD') or '').strip()
    use_tls = bool(current_app.config.get('MAIL_USE_TLS', True))
    sender = str(current_app.config.get('DEFAULT_MAIL_SENDER') or smtp_user or 'noreply@localhost').strip()

    subject = 'Reset your NGO HomeSuite password'
    body = (
        'A password reset was requested for your account.\n\n'
        f'Reset link: {reset_url}\n\n'
        'If you did not request this, you can ignore this message.'
    )

    if not smtp_host:
        current_app.logger.info('password_reset_email smtp_unconfigured reset_url=%s email=%s', reset_url, email)
        return

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = sender
    message['To'] = email
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if smtp_user and smtp_pass:
                smtp.login(smtp_user, smtp_pass)
            smtp.send_message(message)
    except Exception:
        current_app.logger.exception('password_reset_email delivery_failed reset_url=%s email=%s', reset_url, email)


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
    login_template_context = {
        'form': form,
        'dev_login_credentials': _dev_login_credentials(),
        'hide_sso_options': bool(current_app.config.get('HIDE_SSO_OPTIONS', True)),
        'oauth_provider_enabled': {
            name: _oauth_provider_enabled(name) for name in _OAUTH_PROVIDERS
        },
        'support_email': str(current_app.config.get('SUPPORT_EMAIL', '')).strip(),
        'status_page_url': str(current_app.config.get('STATUS_PAGE_URL', '')).strip(),
        'privacy_url': str(current_app.config.get('PRIVACY_URL', '')).strip(),
        'terms_url': str(current_app.config.get('TERMS_URL', '')).strip(),
        'cookies_url': str(current_app.config.get('COOKIES_URL', '')).strip(),
    }
    if form.validate_on_submit():
        identifier = str(form.username.data or '').strip()
        remote_addr = str(request.headers.get('X-Forwarded-For') or request.remote_addr or 'unknown').split(',')[0].strip()
        rl_key = f"{remote_addr}:{identifier.lower()}"
        if current_app.config.get('RATELIMIT_ENABLED', True) and _auth_rate_limited(
            _LOGIN_ATTEMPT_BUCKETS,
            rl_key,
            limit=_LOGIN_RATE_LIMIT,
            window_seconds=_LOGIN_WINDOW_SECONDS,
        ):
            flash('Too many login attempts. Please wait a minute and try again.', 'error')
            return render_template('auth/login.html', **login_template_context), 429

        user = db.session.scalars(
            select(User).where(
                or_(
                    User.username == identifier,
                    func.lower(User.email) == identifier.lower(),
                )
            ).limit(1)
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
                login_template_context.update(
                    {
                        'lockout_active': True,
                        'lockout_remaining_minutes': remaining,
                    }
                )
                return render_template('auth/login.html', **login_template_context)

        if user is None or not user.check_password(form.password.data):
            attempts_left = None
            if user is not None:
                user.failed_login_count = (user.failed_login_count or 0) + 1
                attempts_left = max(_MAX_FAILED_ATTEMPTS - int(user.failed_login_count or 0), 0)
                if user.failed_login_count >= _MAX_FAILED_ATTEMPTS:
                    user.locked_until = (
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        + timedelta(minutes=_LOCKOUT_DURATION_MINUTES)
                    )
                db.session.commit()
            flash('Invalid username or password.', 'error')
            if attempts_left is not None and attempts_left > 0:
                flash(
                    f'For security, this account will lock after {attempts_left} more failed attempt(s).',
                    'warning',
                )
                login_template_context['failed_attempts_remaining'] = attempts_left
            return render_template('auth/login.html', **login_template_context)
        
        if not user.is_active:
            flash('Your account has been deactivated. Please contact support.', 'error')
            return render_template('auth/login.html', **login_template_context)
        
        otp_code = (request.form.get('otp_code') or '').strip()
        if bool(user.mfa_enabled):
            if user.is_mfa_challenge_locked():
                flash('Verification temporarily locked after too many failed codes. Try again later.', 'error')
                return render_template('auth/login.html', mfa_required=True, **login_template_context)
            if not otp_code:
                flash('Verification code required for this account.', 'error')
                return render_template('auth/login.html', mfa_required=True, **login_template_context)
            if not user.verify_mfa_code(otp_code):
                user.register_mfa_challenge_failure(
                    max_attempts=_MAX_MFA_FAILED_ATTEMPTS,
                    window_minutes=_MFA_LOCKOUT_DURATION_MINUTES,
                )
                db.session.commit()
                flash('Invalid verification code.', 'error')
                return render_template('auth/login.html', mfa_required=True, **login_template_context)
            user.reset_mfa_challenge_failures()

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
    
    return render_template('auth/login.html', **login_template_context)


@auth_bp.route('/password/forgot', methods=['GET', 'POST'])
def password_forgot():
    """Request password reset link without exposing account existence."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        remote_addr = str(request.headers.get('X-Forwarded-For') or request.remote_addr or 'unknown').split(',')[0].strip()
        if current_app.config.get('RATELIMIT_ENABLED', True) and _auth_rate_limited(
            _FORGOT_ATTEMPT_BUCKETS,
            remote_addr,
            limit=_FORGOT_RATE_LIMIT,
            window_seconds=_FORGOT_WINDOW_SECONDS,
        ):
            flash('Too many reset requests. Please wait a minute and try again.', 'error')
            return redirect(url_for('auth.password_forgot'))

        email = str(form.email.data or '').strip().lower()
        user = db.session.scalars(
            select(User).where(func.lower(User.email) == email).limit(1)
        ).first()

        if user is not None and bool(user.is_active):
            token = _issue_password_reset_token(user)
            reset_url = url_for('auth.password_reset', token=token, _external=True)
            _dispatch_password_reset_email(email, reset_url)

        flash('If an account exists for that email, a reset link has been sent.', 'info')
        return redirect(url_for('auth.login'))

    return render_template('auth/password_forgot.html', form=form)


@auth_bp.route('/password/reset/<token>', methods=['GET', 'POST'])
def password_reset(token: str):
    """Reset password using signed, time-limited token."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    user = _resolve_user_from_password_reset_token(token)
    if user is None:
        flash('This password reset link is invalid or expired.', 'error')
        return redirect(url_for('auth.password_forgot'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(str(form.password.data or ''))
        user.failed_login_count = 0
        user.locked_until = None
        db.session.commit()
        flash('Your password has been reset. You can now sign in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/password_reset.html', form=form)


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


@auth_bp.get('/mfa/setup')
@login_required
def mfa_setup_page():
    """Render the MFA enrollment workspace."""
    return render_template(
        'auth/mfa_setup.html',
        active_page='security',
        mfa_enabled=bool(current_user.mfa_enabled),
    )


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
        'qr_code_url': f"https://api.qrserver.com/v1/create-qr-code/?size=240x240&data={quote_plus(provisioning_uri)}",
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


@auth_bp.post('/2fa/setup')
@login_required
def two_factor_setup():
    """Compatibility endpoint for 2FA setup clients."""
    return mfa_enroll()


@auth_bp.post('/2fa/verify')
@login_required
def two_factor_verify():
    """Compatibility endpoint for 2FA verification clients."""
    return mfa_confirm()


@auth_bp.post('/2fa/backup-codes')
@login_required
def two_factor_backup_codes():
    """Rotate and return fresh backup codes for the current user."""
    return mfa_rotate_backup_codes()


@auth_bp.post('/2fa/login')
def two_factor_login():
    """API login endpoint that requires TOTP/backup code when MFA is enabled."""
    payload = _request_data()
    username = str(payload.get('username') or '').strip()
    password = str(payload.get('password') or '')
    otp_code = str(payload.get('otp_code') or '').strip()

    if not username or not password:
        return {'error': 'username and password are required'}, 400

    user = db.session.scalars(
        select(User).where(User.username == username).limit(1)
    ).first()
    if user is None or not user.check_password(password):
        return {'error': 'invalid credentials'}, 401

    if not user.is_active:
        return {'error': 'account is inactive'}, 403

    if bool(user.mfa_enabled):
        if user.is_mfa_challenge_locked():
            return {'error': 'verification temporarily locked, try again later'}, 429
        if not otp_code:
            return {'error': 'otp_code is required for this account'}, 401
        if not user.verify_mfa_code(otp_code):
            user.register_mfa_challenge_failure(
                max_attempts=_MAX_MFA_FAILED_ATTEMPTS,
                window_minutes=_MFA_LOCKOUT_DURATION_MINUTES,
            )
            db.session.commit()
            return {'error': 'invalid verification code'}, 401
        user.reset_mfa_challenge_failures()

    user.failed_login_count = 0
    user.locked_until = None
    db.session.commit()

    session.clear()
    login_user(user, remember=False)
    return {
        'status': 'ok',
        'user_id': int(user.id),
        'username': user.username,
        'mfa_enabled': bool(user.mfa_enabled),
    }, 200


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
    'microsoft': {
        'server_metadata_url': 'https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration',
        'client_kwargs': {'scope': 'openid email profile User.Read'},
    },
    'okta': {
        # Value is overridden from app config during initialization.
        'server_metadata_url': '',
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
        provider_params = dict(params)
        if name == 'okta':
            okta_metadata_url = str(app.config.get('OKTA_SERVER_METADATA_URL', '')).strip()
            if not okta_metadata_url:
                app.logger.info('oauth_provider=%s status=skipped reason=missing_okta_metadata_url', name)
                continue
            provider_params['server_metadata_url'] = okta_metadata_url
        if client_id and client_secret:
            _oauth.register(name=name, client_id=client_id, client_secret=client_secret, **provider_params)
            app.logger.info(
                'oauth_provider=%s status=registered client_id=%s client_secret=%s',
                name,
                _mask_config_value(str(client_id)),
                _mask_config_value(str(client_secret)),
            )
        else:
            app.logger.info(
                'oauth_provider=%s status=skipped client_id=%s client_secret=%s',
                name,
                _mask_config_value(str(client_id)),
                _mask_config_value(str(client_secret)),
            )


@auth_bp.get('/oauth/providers')
def oauth_provider_status():
    """Return configured/registered status for each OAuth provider.

    Useful for quick backend sanity checks before testing frontend buttons.
    """
    return {
        'providers': _oauth_provider_diagnostics(),
        'host': request.host_url.rstrip('/'),
    }, 200


def _get_oauth_userinfo(provider_name: str, token: dict) -> tuple[str | None, str | None, str | None]:
    """Return (provider_user_id, email, display_name) from an OAuth token/userinfo response.

    Returns (None, None, None) when the provider response cannot be parsed.
    """
    client = _oauth.create_client(provider_name)
    if provider_name in {'google', 'microsoft', 'okta'}:
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


def _assert_google_oauth_env() -> None:
    """Fail fast when Google OAuth env vars are missing to avoid silent config drift."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    assert client_id, "Missing GOOGLE_CLIENT_ID"
    assert client_secret, "Missing GOOGLE_CLIENT_SECRET"


@auth_bp.get('/oauth/<provider>')
def oauth_login(provider: str):
    """Redirect to OAuth provider's authorization page."""
    if provider not in _OAUTH_PROVIDERS:
        flash('Unknown OAuth provider.', 'error')
        return redirect(url_for('auth.login'))

    if provider == 'google':
        _assert_google_oauth_env()

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

    if provider == 'google':
        _assert_google_oauth_env()

    client = _oauth.create_client(provider)
    if client is None:
        flash(f'{provider.title()} login is not configured on this server.', 'error')
        return redirect(url_for('auth.login'))

    try:
        token = client.authorize_access_token()
    except Exception:
        current_app.logger.exception('oauth_callback token_exchange_failed provider=%s', provider)
        flash('OAuth authorization failed. Please try again.', 'error')
        return redirect(url_for('auth.login'))

    provider_uid, email, display_name = _get_oauth_userinfo(provider, token)
    if not provider_uid or not email:
        current_app.logger.error(
            'oauth_callback userinfo_incomplete provider=%s provider_uid=%s email=%s',
            provider,
            bool(provider_uid),
            bool(email),
        )
        flash('Could not retrieve account information from the provider.', 'error')
        return redirect(url_for('auth.login'))

    try:
        identity = NormalizedIdentity.from_oauth(
            provider=provider,
            provider_user_id=provider_uid,
            email=email,
            display_name=display_name,
        )
    except ValueError:
        current_app.logger.error('oauth_callback identity_normalization_failed provider=%s', provider)
        flash('Could not normalize provider identity. Please try again.', 'error')
        return redirect(url_for('auth.login'))

    # 1. Look for existing user linked to this provider identity.
    user = db.session.scalars(
        select(User).where(
            User.oauth_provider == identity.provider,
            User.oauth_provider_id == identity.provider_user_id,
        ).limit(1)
    ).first()

    if user is None:
        # 2. Try to link by email (existing password account).
        user = db.session.scalars(
            select(User).where(func.lower(User.email) == identity.email_normalized).limit(1)
        ).first()
        if user is not None:
            user.oauth_provider = identity.provider
            user.oauth_provider_id = identity.provider_user_id
            db.session.commit()

    if user is None:
        # 3. Auto-create a new account from the OAuth identity.
        parts = (identity.display_name or '').split(' ', 1)
        first = parts[0] if parts else ''
        last = parts[1] if len(parts) > 1 else ''
        base_username = (identity.email.split('@')[0] or identity.provider)[:75].replace(' ', '_')
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
            email=identity.email,
            first_name=first,
            last_name=last,
            password_hash='!oauth',   # sentinel — not a valid argon2 hash
            role='viewer',
            oauth_provider=identity.provider,
            oauth_provider_id=identity.provider_user_id,
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


# ---------------------------------------------------------------------------
# 2FA Enforcement Policy (A-2)
# ---------------------------------------------------------------------------

_EXEMPT_2FA_ENDPOINTS: set[str] = {
    'auth.login',
    'auth.logout',
    'auth.password_forgot',
    'auth.password_reset',
    'auth.register',
    'auth.mfa_setup_page',
    'auth.mfa_enroll',
    'auth.mfa_confirm',
    'auth.mfa_disable',
    'auth.mfa_rotate_backup_codes',
    'auth.two_factor_setup',
    'auth.two_factor_verify',
    'auth.two_factor_backup_codes',
    'auth.two_factor_login',
    'auth.step_up_otp',
    'static',
}


def _role_requires_2fa(role: str) -> bool:
    """Return True if the given role must enroll in TOTP before accessing the app."""
    configured_roles = current_app.config.get('ROLES_REQUIRING_2FA')
    roles: list[str] = list(configured_roles) if configured_roles is not None else ['admin']
    return str(role or '').strip().lower() in {r.strip().lower() for r in roles}


def _2fa_enforcement_check() -> None:
    """Before-request hook: redirect to MFA setup if role requires 2FA and user has not enrolled.

    Only applies to authenticated users whose role is in ROLES_REQUIRING_2FA.
    MFA and auth endpoints are exempted to avoid redirect loops.
    """
    if not current_user.is_authenticated:
        return
    endpoint = request.endpoint or ''
    if endpoint in _EXEMPT_2FA_ENDPOINTS or endpoint.startswith('auth.') or endpoint.startswith('static'):
        return
    role = str(getattr(current_user, 'role', '') or '').strip().lower()
    if _role_requires_2fa(role) and not bool(getattr(current_user, 'mfa_enabled', False)):
        from ngo_homesuite.audit.security_events import SecurityAuditService, SecurityEventType
        try:
            SecurityAuditService.log_event(
                event_type=SecurityEventType.PERMISSION_DENIED,
                action='POLICY_2FA_ENFORCEMENT_TRIGGERED',
                result='redirect_to_mfa_setup',
                payload={'role': role, 'endpoint': endpoint, 'user_id': int(current_user.id)},
            )
        except Exception:
            current_app.logger.warning(
                '2fa_enforcement_audit_log_failed endpoint=%s user_id=%s',
                endpoint,
                int(getattr(current_user, 'id', 0) or 0),
            )
        flash('Your role requires Two-Factor Authentication. Please enroll to continue.', 'warning')
        return redirect(url_for('auth.mfa_setup_page'))


# ---------------------------------------------------------------------------
# A-3: Step-up authentication
# ---------------------------------------------------------------------------

_STEP_UP_SESSION_KEY = '_step_up_verified_at'


def is_step_up_verified() -> bool:
    """Return True if the current session has a valid step-up auth token."""
    verified_at = session.get(_STEP_UP_SESSION_KEY)
    if not verified_at:
        return False
    ttl = int(current_app.config.get('STEP_UP_AUTH_TTL_SECONDS', 900))
    age = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(str(verified_at))).total_seconds()
    return age < ttl


def require_step_up_auth(fn):
    """Decorator: require a recently-verified step-up OTP before executing the view.

    Usage::

        @admin_bp.post('/users/<int:user_id>/delete')
        @login_required
        @roles_required('admin')
        @require_step_up_auth
        def delete_user(user_id):
            ...

    Returns 403 JSON with ``step_up_required: true`` when the step-up token is
    absent or expired.  The client should redirect the user to ``POST /auth/step-up-otp``
    and then retry the original request.
    """
    from functools import wraps
    from flask import jsonify

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_step_up_verified():
            from ngo_homesuite.audit.security_events import SecurityAuditService, SecurityEventType
            SecurityAuditService.log_event(
                event_type=SecurityEventType.PERMISSION_DENIED,
                action='SENSITIVE_ACTION_ATTEMPTED',
                result='step_up_required',
                payload={
                    'endpoint': request.endpoint,
                    'user_id': int(getattr(current_user, 'id', 0)),
                },
            )
            return jsonify({'error': 'Step-up authentication required', 'step_up_required': True}), 403
        return fn(*args, **kwargs)

    return wrapper


@auth_bp.post('/step-up-otp')
@login_required
def step_up_otp():
    """Verify a TOTP/backup code and grant a short-lived step-up session token.

    Request body (JSON or form): ``{"code": "<6-digit TOTP or backup code>"}``

    Emits audit events:
    - STEP_UP_OTP_VERIFIED on success
    - STEP_UP_OTP_FAILED on invalid code
    """
    from ngo_homesuite.audit.security_events import SecurityAuditService, SecurityEventType

    if not bool(current_user.mfa_enabled):
        return {'error': 'MFA is not enabled for this account'}, 400

    data = _request_data()
    code = str(data.get('code') or '').strip()
    if not code:
        return {'error': 'code is required'}, 400

    if not current_user.verify_mfa_code(code):
        db.session.commit()
        SecurityAuditService.log_event(
            event_type=SecurityEventType.LOGIN_FAILURE,
            action='STEP_UP_OTP_FAILED',
            result='invalid_code',
            payload={'user_id': int(current_user.id)},
        )
        return {'error': 'invalid code'}, 400

    # Record step-up verification timestamp in session.
    session[_STEP_UP_SESSION_KEY] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    db.session.commit()

    SecurityAuditService.log_event(
        event_type=SecurityEventType.LOGIN_SUCCESS,
        action='STEP_UP_OTP_VERIFIED',
        result='success',
        payload={'user_id': int(current_user.id)},
    )
    ttl = int(current_app.config.get('STEP_UP_AUTH_TTL_SECONDS', 900))
    return {'status': 'verified', 'expires_in_seconds': ttl}, 200
