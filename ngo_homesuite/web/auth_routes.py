"""
Authentication blueprint for NGO HomeSuite.

Handles user login, registration, logout, and password management.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, abort
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
