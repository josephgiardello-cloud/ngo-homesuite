"""
Flask-SQLAlchemy models for NGO HomeSuite.

Core entities:
- User: System users with roles (admin, fundraiser, volunteer_manager, viewer)
- Organization: NGO organization/tenant
- Beneficiary: Individual benefiting from the organization
- Project: Initiative/project within the organization
- Donation: Financial contribution to the organization
"""

import hashlib
import hmac
import os
import re
import secrets
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
import bcrypt
from flask import current_app, has_app_context
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import event, text
from sqlalchemy.dialects.sqlite import JSON
import pyotp

db = SQLAlchemy()
password_hasher = PasswordHasher()
_MFA_SECRET_PREFIX = "enc:"


def _mfa_fernet() -> Fernet | None:
    raw_key = None
    if has_app_context():
        raw_key = current_app.config.get("MFA_TOTP_ENCRYPTION_KEY")
    if not raw_key:
        raw_key = os.environ.get("MFA_TOTP_ENCRYPTION_KEY")
    if not raw_key and has_app_context():
        raw_key = current_app.config.get("SECRET_KEY")
    if not raw_key:
        raw_key = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if not raw_key:
        return None
    digest = hashlib.sha256(str(raw_key).encode("utf-8")).digest()
    return Fernet(urlsafe_b64encode(digest))


def _encrypt_mfa_secret(secret: str) -> str:
    if not secret:
        return ""
    if secret.startswith(_MFA_SECRET_PREFIX):
        return secret
    fernet = _mfa_fernet()
    if fernet is None:
        raise RuntimeError("MFA secret encryption key is not configured")
    token = fernet.encrypt(secret.encode("utf-8")).decode("utf-8")
    return f"{_MFA_SECRET_PREFIX}{token}"


def _decrypt_mfa_secret(stored_secret: str | None) -> str | None:
    if not stored_secret:
        return None
    fernet = _mfa_fernet()
    if fernet is None:
        raise RuntimeError("MFA secret encryption key is not configured")
    text = str(stored_secret)
    if not text.startswith(_MFA_SECRET_PREFIX):
        return text
    token = text[len(_MFA_SECRET_PREFIX):]
    try:
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        raise RuntimeError("Stored MFA secret could not be decrypted")

def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _slugify_value(value: str, *, fallback_prefix: str) -> str:
    raw = (value or "").strip().lower()
    raw = re.sub(r"[^\w\s-]", "", raw)
    raw = re.sub(r"[\s_]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw[:100] or f"{fallback_prefix}-{secrets.token_hex(4)}"



class User(UserMixin, db.Model):
    """System user with authentication and role-based access control."""
    
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    
    # User info
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    
    # Role and status
    role = db.Column(db.String(32), default='viewer', nullable=False)  # admin, staff, volunteer, viewer
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    can_authorize_external_comms = db.Column(db.Boolean, default=False, nullable=False)
    totp_required_flag = db.Column(db.Boolean, default=False, nullable=False)
    
    # Organization association
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=True)
    organization = db.relationship('Organization', backref='users')
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)
    last_login = db.Column(db.DateTime, nullable=True)

    # Account lockout after repeated failed logins
    failed_login_count = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)

    # Multi-factor authentication (TOTP + one-time backup codes)
    mfa_enabled = db.Column(db.Boolean, default=False, nullable=False)
    mfa_totp_secret = db.Column(db.String(255), nullable=True)
    mfa_backup_codes_json = db.Column(JSON, nullable=True)
    mfa_failed_attempts = db.Column(db.Integer, default=0, nullable=False)
    mfa_attempt_window_started_at = db.Column(db.DateTime, nullable=True)
    mfa_locked_until = db.Column(db.DateTime, nullable=True)

    # OAuth / SSO login
    oauth_provider = db.Column(db.String(32), nullable=True, index=True)     # 'google', 'github'
    oauth_provider_id = db.Column(db.String(256), nullable=True, index=True)  # provider user-id

    # WebAuthn / passkeys (multiple credentials per account)
    webauthn_credentials_json = db.Column(JSON, nullable=True)

    # Per-user UI personalization (sidebar collapse, favorites, recents)
    ui_profile_json = db.Column(JSON, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('oauth_provider', 'oauth_provider_id', name='uq_user_oauth_provider'),
    )
    
    def set_password(self, password):
        """Hash and set password."""
        self.password_hash = password_hasher.hash(password)
    
    def check_password(self, password):
        """Verify password against hash."""
        try:
            return password_hasher.verify(self.password_hash, password)
        except (VerifyMismatchError, VerificationError):
            return False
    
    def has_role(self, *roles):
        """Check if user has any of the specified roles."""
        role_map = {
            'fundraiser': 'staff',
            'volunteer_manager': 'volunteer',
        }
        normalized_self = role_map.get(self.role, self.role)
        normalized_roles = {role_map.get(role, role) for role in roles}
        return normalized_self in normalized_roles
    
    def __repr__(self):
        return f'<User {self.username}>'

    def ensure_mfa_secret(self) -> str:
        """Create and persist a TOTP secret if the user does not already have one."""
        current_secret = _decrypt_mfa_secret(self.mfa_totp_secret)
        if not current_secret:
            current_secret = pyotp.random_base32()
            self.mfa_totp_secret = _encrypt_mfa_secret(current_secret)
            return current_secret

        # Opportunistically migrate legacy plaintext values to encrypted-at-rest.
        if self.mfa_totp_secret and not str(self.mfa_totp_secret).startswith(_MFA_SECRET_PREFIX):
            self.mfa_totp_secret = _encrypt_mfa_secret(current_secret)
        return current_secret

    def mfa_provisioning_uri(self, issuer_name: str = 'NGO HomeSuite') -> str:
        """Return otpauth provisioning URI for authenticator apps."""
        secret = self.ensure_mfa_secret()
        totp = pyotp.TOTP(secret)
        account = self.email or self.username
        return totp.provisioning_uri(name=account, issuer_name=issuer_name)

    def generate_mfa_backup_codes(self, count: int = 10) -> list[str]:
        """Generate one-time backup codes and store bcrypt hashes."""
        generated: list[str] = []
        stored_hashes: list[str] = []
        for _ in range(max(1, int(count))):
            code = secrets.token_hex(4).upper()
            generated.append(code)
            hashed = bcrypt.hashpw(code.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')
            stored_hashes.append(hashed)
        self.mfa_backup_codes_json = stored_hashes
        return generated

    def is_mfa_challenge_locked(self) -> bool:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return bool(self.mfa_locked_until and self.mfa_locked_until > now)

    def register_mfa_challenge_failure(self, *, max_attempts: int = 5, window_minutes: int = 15) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        window_start = self.mfa_attempt_window_started_at
        if window_start is None or (now - window_start).total_seconds() > max(1, int(window_minutes)) * 60:
            self.mfa_attempt_window_started_at = now
            self.mfa_failed_attempts = 0
        self.mfa_failed_attempts = int(self.mfa_failed_attempts or 0) + 1
        if self.mfa_failed_attempts >= max(1, int(max_attempts)):
            self.mfa_locked_until = now + timedelta(minutes=max(1, int(window_minutes)))

    def reset_mfa_challenge_failures(self) -> None:
        self.mfa_failed_attempts = 0
        self.mfa_attempt_window_started_at = None
        self.mfa_locked_until = None

    def verify_mfa_code(self, code: str, *, valid_window: int = 1) -> bool:
        """Verify TOTP code or one-time backup code.

        Backup codes are consumed after successful verification.
        """
        raw = (code or '').strip().replace(' ', '')
        if not raw:
            return False

        resolved_secret = _decrypt_mfa_secret(self.mfa_totp_secret)
        if resolved_secret:
            try:
                if pyotp.TOTP(resolved_secret).verify(raw, valid_window=valid_window):
                    return True
            except Exception:
                pass

        current_hashes = [str(item) for item in list(self.mfa_backup_codes_json or []) if item]
        for stored_hash in current_hashes:
            try:
                if stored_hash.startswith('$2'):
                    if bcrypt.checkpw(raw.encode('utf-8'), stored_hash.encode('utf-8')):
                        current_hashes.remove(stored_hash)
                        self.mfa_backup_codes_json = current_hashes
                        return True
                else:
                    # Legacy compatibility for old sha256 stored values.
                    legacy_hash = hashlib.sha256(raw.upper().encode('utf-8')).hexdigest()
                    if hmac.compare_digest(legacy_hash, stored_hash):
                        current_hashes.remove(stored_hash)
                        self.mfa_backup_codes_json = current_hashes
                        return True
            except Exception:
                continue

        return False


class Organization(db.Model):
    """NGO organization/tenant entity."""
    
    __tablename__ = 'organizations'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    slug = db.Column(db.String(100), unique=True, nullable=False)  # URL-friendly name
    
    # Organization details
    description = db.Column(db.Text, nullable=True)
    mission = db.Column(db.Text, nullable=True)
    website = db.Column(db.String(200), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    
    # Location
    country = db.Column(db.String(100), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    
    # Status
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    
    # Metadata
    metadata_json = db.Column('metadata', JSON, nullable=True)  # For additional custom fields
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)
    
    # Relationships
    beneficiaries = db.relationship('Beneficiary', backref='organization', cascade='all, delete-orphan')
    projects = db.relationship('Project', backref='organization', cascade='all, delete-orphan')
    donations = db.relationship('Donation', backref='organization', cascade='all, delete-orphan')
    donors = db.relationship('Donor', backref='organization', cascade='all, delete-orphan')
    funds = db.relationship('Fund', backref='organization', cascade='all, delete-orphan')
    volunteers = db.relationship('Volunteer', backref='organization', cascade='all, delete-orphan')
    expenses = db.relationship('Expense', backref='organization', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Organization {self.name}>'


@event.listens_for(Organization, 'before_insert')
def _organization_before_insert(_mapper, connection, target) -> None:
    if getattr(target, 'slug', None):
        return

    base_slug = _slugify_value(getattr(target, 'name', ''), fallback_prefix='org')
    slug = base_slug
    counter = 1

    while connection.execute(
        text("SELECT 1 FROM organizations WHERE slug = :slug LIMIT 1"),
        {"slug": slug},
    ).first() is not None:
        suffix = f"-{counter}"
        slug = f"{base_slug[: max(1, 100 - len(suffix))]}{suffix}"
        counter += 1

    target.slug = slug


class Beneficiary(db.Model):
    """Individual benefiting from the organization's programs."""
    
    __tablename__ = 'beneficiaries'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    
    # Personal info
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    
    # Demographics
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(20), nullable=True)  # e.g., 'M', 'F', 'Other', 'Prefer not to say'
    
    # Location
    country = db.Column(db.String(100), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    
    # Program info
    program = db.Column(db.String(200), nullable=True)  # e.g., 'Education', 'Health', 'Livelihood'
    status = db.Column(db.String(50), default='active', nullable=False)  # active, inactive, completed
    notes = db.Column(db.Text, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)
    
    def __repr__(self):
        return f'<Beneficiary {self.first_name} {self.last_name}>'


class Project(db.Model):
    """Initiative/project within the organization."""
    
    __tablename__ = 'projects'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    
    # Project info
    name = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    program = db.Column(db.String(200), nullable=True)
    
    # Dates
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    
    # Budget
    budget = db.Column(db.Float, default=0.0, nullable=False)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    spent = db.Column(db.Float, default=0.0, nullable=False)
    
    # Status
    status = db.Column(db.String(50), default='planned', nullable=False)  # planned, active, paused, completed
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)
    
    # Relationships
    donations = db.relationship('Donation', backref='project')
    
    def __repr__(self):
        return f'<Project {self.name}>'


class Donation(db.Model):
    """Financial contribution to the organization or project."""
    
    __tablename__ = 'donations'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=True, index=True)
    fund_id = db.Column(db.Integer, db.ForeignKey('funds.id'), nullable=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True)
    
    # Donor info
    donor_name = db.Column(db.String(200), nullable=False)
    donor_email = db.Column(db.String(120), nullable=True)
    donor_phone = db.Column(db.String(20), nullable=True)
    
    # Donation details
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    donation_date = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)
    
    # Payment info
    payment_method = db.Column(db.String(50), nullable=True)  # e.g., 'credit_card', 'bank_transfer', 'cash'
    channel = db.Column(db.String(50), nullable=True)  # web, event, mail, phone, p2p, grant_portal
    reference_number = db.Column(db.String(100), nullable=True, unique=True)
    
    # Status
    status = db.Column(db.String(50), default='received', nullable=False)  # received, processed, receipted
    
    # Purpose
    purpose = db.Column(db.String(200), nullable=True)  # e.g., 'General Fund', 'Emergency Relief', 'Specific Project'
    is_anonymous = db.Column(db.Boolean, default=False, nullable=False)
    public_display_name = db.Column(db.String(200), nullable=True)
    tribute_type = db.Column(db.String(50), nullable=True)  # in_honor_of, in_memory_of
    tribute_honoree_name = db.Column(db.String(200), nullable=True)
    tribute_honoree_contact = db.Column(db.String(255), nullable=True)
    soft_credit_name = db.Column(db.String(200), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)
    
    def __repr__(self):
        return f'<Donation {self.amount} {self.currency}>'

    __mapper_args__ = {
        'version_id_col': version_id,
    }


class RecurringDonationPlan(db.Model):
    """Recurring donation instructions tied to a donor profile."""

    __tablename__ = 'recurring_donation_plans'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    frequency = db.Column(db.String(20), default='monthly', nullable=False)  # monthly, quarterly, yearly
    payment_method = db.Column(db.String(50), nullable=True)
    purpose = db.Column(db.String(200), nullable=True)
    next_charge_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(20), default='active', nullable=False)  # active, paused, failed, cancelled
    fail_count = db.Column(db.Integer, default=0, nullable=False)
    last_error = db.Column(db.Text, nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    donor = db.relationship('Donor', backref='recurring_plans')

    def __repr__(self):
        return f'<RecurringDonationPlan donor={self.donor_id} {self.frequency} {self.amount} {self.currency}>'

    __mapper_args__ = {
        'version_id_col': version_id,
    }


class DonationReceipt(db.Model):
    """Receipt generation/send tracking for a donation."""

    __tablename__ = 'donation_receipts'

    id = db.Column(db.Integer, primary_key=True)
    donation_id = db.Column(db.Integer, db.ForeignKey('donations.id'), nullable=False, unique=True, index=True)
    receipt_number = db.Column(db.String(64), nullable=False, unique=True, index=True)
    status = db.Column(db.String(20), default='generated', nullable=False)  # generated, sent, failed
    sent_to_email = db.Column(db.String(120), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    donation = db.relationship('Donation', backref='receipt')

    def __repr__(self):
        return f'<DonationReceipt donation={self.donation_id} status={self.status}>'


class DonorSoftCredit(db.Model):
    """Relational attribution linking an influencer donor to a donation."""

    __tablename__ = 'donor_soft_credits'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donation_id = db.Column(db.Integer, db.ForeignKey('donations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=False, index=True)
    role = db.Column(db.String(50), nullable=False, default='influencer')  # influencer, solicitor, steward
    credited_amount = db.Column(db.Float, nullable=False, default=0.0)
    credit_weight = db.Column(db.Float, nullable=False, default=1.0)
    rationale = db.Column(db.Text, nullable=True)
    attributed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)

    donation = db.relationship('Donation', backref='soft_credits')
    donor = db.relationship('Donor', backref='soft_credits')
    attributed_by = db.relationship('User', backref='donor_soft_credits')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'donation_id', 'donor_id', 'role', name='uq_soft_credit_org_donation_donor_role'),
    )

    def __repr__(self):
        return f'<DonorSoftCredit donation={self.donation_id} donor={self.donor_id} role={self.role}>'


class Donor(db.Model):
    """Donor profile and contact information."""

    __tablename__ = 'donors'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    email = db.Column(db.String(120), nullable=True, index=True)
    phone = db.Column(db.String(20), nullable=True)
    salutation = db.Column(db.String(50), nullable=True)
    preferred_name = db.Column(db.String(200), nullable=True)
    donor_type = db.Column(db.String(50), default='individual', nullable=False)
    status = db.Column(db.String(50), default='active', nullable=False)
    photo_path = db.Column(db.String(300), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    country = db.Column(db.String(100), nullable=True)
    postal_code = db.Column(db.String(20), nullable=True)
    preferred_contact_method = db.Column(db.String(20), default='email', nullable=False)
    communication_opt_in = db.Column(db.Boolean, default=True, nullable=False)
    employer = db.Column(db.String(200), nullable=True)
    source = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    donations = db.relationship('Donation', backref='donor')

    def __repr__(self):
        return f'<Donor {self.name}>'


class Fund(db.Model):
    """Fund that donations and expenses can be allocated to."""

    __tablename__ = 'funds'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    donations = db.relationship('Donation', backref='fund')
    expenses = db.relationship('Expense', backref='fund')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'name', name='uq_funds_org_name'),
    )

    def __repr__(self):
        return f'<Fund {self.name}>'

    __mapper_args__ = {
        'version_id_col': version_id,
    }


class Volunteer(db.Model):
    """Volunteer record for activity and contact management."""

    __tablename__ = 'volunteers'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), nullable=True, index=True)
    phone = db.Column(db.String(20), nullable=True)
    hours_logged = db.Column(db.Float, default=0.0, nullable=False)
    status = db.Column(db.String(50), default='active', nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    def __repr__(self):
        return f'<Volunteer {self.name}>'


class Expense(db.Model):
    """Expense captured for project/program and fund reporting."""

    __tablename__ = 'expenses'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    fund_id = db.Column(db.Integer, db.ForeignKey('funds.id'), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    paid_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    payee = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    project = db.relationship('Project', backref='expenses')

    def __repr__(self):
        return f'<Expense {self.amount} {self.currency}>'


class AIConversation(db.Model):
    """Persisted AI assistant conversation session."""

    __tablename__ = 'ai_conversations'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=True, index=True)
    model = db.Column(db.String(100), nullable=True)
    tenant_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    messages = db.relationship('AIMessage', backref='conversation', cascade='all, delete-orphan', order_by='AIMessage.id')

    def __repr__(self):
        return f'<AIConversation {self.session_id}>'


class AIMessage(db.Model):
    """Single message in an AI conversation."""

    __tablename__ = 'ai_messages'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('ai_conversations.id'), nullable=False, index=True)
    role = db.Column(db.String(16), nullable=False)   # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    prompt_sha256 = db.Column(db.String(64), nullable=True)   # hash of user message for audit
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    def __repr__(self):
        return f'<AIMessage {self.role} conv={self.conversation_id}>'


from ngo_homesuite.grants.models import Grant
from ngo_homesuite.grants.models import GrantApprovalChainConfig
from ngo_homesuite.grants.models import GrantApprovalDecision
from ngo_homesuite.grants.models import GrantApprovalRequest
from ngo_homesuite.grants.models import GrantBudgetLine
from ngo_homesuite.grants.models import GrantDisbursement
from ngo_homesuite.grants.models import GrantExpenseAllocation
from ngo_homesuite.grants.models import GrantOpportunity
from ngo_homesuite.grants.models import GrantOutcomeRecord
from ngo_homesuite.grants.models import GrantOutcomeTemplate
from ngo_homesuite.grants.models import GrantProposal
from ngo_homesuite.grants.models import GrantScore
from ngo_homesuite.grants.models import GrantSearchAlert
from ngo_homesuite.grants.models import GrantSearchProfile


class MembershipTier(db.Model):
    """Configurable membership tier for an organization."""

    __tablename__ = 'membership_tiers'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    interval = db.Column(db.String(20), default='annual', nullable=False)  # monthly, quarterly, annual
    benefits = db.Column(db.Text, nullable=True)   # newline-separated list of perks
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    records = db.relationship('MembershipRecord', backref='tier', cascade='all, delete-orphan')
    organization = db.relationship('Organization', backref='membership_tiers')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'name', name='uq_membership_tier_org_name'),
    )

    def __repr__(self):
        return f'<MembershipTier {self.name}>'


class MembershipRecord(db.Model):
    """Active or historical membership for a donor."""

    __tablename__ = 'membership_records'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=False, index=True)
    tier_id = db.Column(db.Integer, db.ForeignKey('membership_tiers.id'), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True, index=True)
    next_renewal_date = db.Column(db.Date, nullable=True, index=True)
    status = db.Column(db.String(20), default='active', nullable=False, index=True)  # active, lapsed, cancelled
    payment_reference = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    donor = db.relationship('Donor', backref='memberships')

    def __repr__(self):
        return f'<MembershipRecord donor={self.donor_id} tier={self.tier_id} [{self.status}]>'


class StewardshipJourney(db.Model):
    """Named automated communication sequence definition."""

    __tablename__ = 'stewardship_journeys'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    trigger = db.Column(db.String(50), nullable=False)  # new_donor, lybunt, anniversary, lapsed_member
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    steps = db.relationship('StewardshipStep', backref='journey', cascade='all, delete-orphan', order_by='StewardshipStep.step_order')
    organization = db.relationship('Organization', backref='stewardship_journeys')

    def __repr__(self):
        return f'<StewardshipJourney {self.name}>'


class StewardshipStep(db.Model):
    """One step in a stewardship journey (send email, send SMS, wait)."""

    __tablename__ = 'stewardship_steps'

    id = db.Column(db.Integer, primary_key=True)
    journey_id = db.Column(db.Integer, db.ForeignKey('stewardship_journeys.id'), nullable=False, index=True)
    step_order = db.Column(db.Integer, nullable=False, default=0)
    step_type = db.Column(db.String(20), nullable=False)  # email, sms, wait
    delay_days = db.Column(db.Integer, default=0, nullable=False)  # days after previous step
    template_name = db.Column(db.String(100), nullable=True)  # email/SMS template key
    subject = db.Column(db.String(300), nullable=True)
    body = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    def __repr__(self):
        return f'<StewardshipStep journey={self.journey_id} order={self.step_order} {self.step_type}>'


class StewardshipEnrollment(db.Model):
    """Tracks a donor's progress through a stewardship journey."""

    __tablename__ = 'stewardship_enrollments'

    id = db.Column(db.Integer, primary_key=True)
    journey_id = db.Column(db.Integer, db.ForeignKey('stewardship_journeys.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    current_step = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(20), default='active', nullable=False)  # active, completed, cancelled
    enrolled_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    next_step_due = db.Column(db.DateTime, nullable=True, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    donor = db.relationship('Donor', backref='journey_enrollments')
    journey = db.relationship('StewardshipJourney', backref='enrollments')

    def __repr__(self):
        return f'<StewardshipEnrollment journey={self.journey_id} donor={self.donor_id}>'


class DonorJourneyAutomationEvent(db.Model):
    """Durable automation run log used for idempotency, cooldowns, and audit."""

    __tablename__ = 'donor_journey_automation_events'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True, index=True)
    recurring_plan_id = db.Column(db.Integer, db.ForeignKey('recurring_donation_plans.id'), nullable=True, index=True)
    trigger_name = db.Column(db.String(80), nullable=False, index=True)
    action_type = db.Column(db.String(80), nullable=False, index=True)
    status = db.Column(db.String(30), default='executed', nullable=False, index=True)  # executed, skipped, failed
    idempotency_key = db.Column(db.String(200), nullable=False)
    cooldown_until = db.Column(db.DateTime, nullable=True)
    reason = db.Column(db.Text, nullable=True)
    payload_json = db.Column(JSON, nullable=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    related_task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=True)
    related_enrollment_id = db.Column(db.Integer, db.ForeignKey('stewardship_enrollments.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'idempotency_key', name='uq_donor_journey_automation_org_key'),
    )

    donor = db.relationship('Donor', backref='automation_events')
    recurring_plan = db.relationship('RecurringDonationPlan', backref='automation_events')
    actor_user = db.relationship('User', backref='automation_events')
    related_task = db.relationship('Task', backref='automation_events')
    related_enrollment = db.relationship('StewardshipEnrollment', backref='automation_events')

    def __repr__(self):
        return f'<DonorJourneyAutomationEvent trigger={self.trigger_name} action={self.action_type} status={self.status}>'


class FormSubmissionEvent(db.Model):
    """Durable integrated-form intake ledger with downstream CRM links."""

    __tablename__ = 'form_submission_events'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    source = db.Column(db.String(80), nullable=False, index=True)
    form_name = db.Column(db.String(200), nullable=True)
    form_type = db.Column(db.String(50), nullable=False, index=True)
    external_submission_id = db.Column(db.String(200), nullable=True, index=True)
    idempotency_key = db.Column(db.String(200), nullable=False)

    submitter_name = db.Column(db.String(200), nullable=True)
    submitter_email = db.Column(db.String(255), nullable=True, index=True)
    submitter_phone = db.Column(db.String(40), nullable=True)

    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True, index=True)
    donation_id = db.Column(db.Integer, db.ForeignKey('donations.id'), nullable=True, index=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=True, index=True)

    amount = db.Column(db.Float, nullable=True)
    currency = db.Column(db.String(3), nullable=True)
    message = db.Column(db.Text, nullable=True)
    metadata_json = db.Column(JSON, nullable=True)
    raw_payload_json = db.Column(JSON, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True, index=True)
    processed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), default='processed', nullable=False, index=True)
    error_message = db.Column(db.Text, nullable=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'idempotency_key', name='uq_form_submission_event_org_key'),
    )

    donor = db.relationship('Donor', backref='form_submission_events')
    donation = db.relationship('Donation', backref='form_submission_events')
    task = db.relationship('Task', backref='form_submission_events')
    actor_user = db.relationship('User', backref='form_submission_events')

    def __repr__(self):
        return f'<FormSubmissionEvent source={self.source} type={self.form_type} status={self.status}>'


# ---------------------------------------------------------------------------
# Task Management (Moves Management)
# ---------------------------------------------------------------------------

class Task(db.Model):
    """Actionable task linked to a donor, grant, donation, or project."""

    __tablename__ = 'tasks'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    # Polymorphic target (one optional FK per entity type)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True, index=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=True, index=True)
    donation_id = db.Column(db.Integer, db.ForeignKey('donations.id'), nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)

    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    task_type = db.Column(db.String(50), default='general', nullable=False)  # call, email, meeting, follow_up, general
    priority = db.Column(db.String(20), default='medium', nullable=False)   # low, medium, high, urgent
    status = db.Column(db.String(20), default='open', nullable=False, index=True)  # open, in_progress, done, cancelled
    due_date = db.Column(db.DateTime, nullable=True, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    # Reminder tracking
    reminder_channel = db.Column(db.String(20), default='email', nullable=False)  # email, sms, auto, none
    reminder_sent_count = db.Column(db.Integer, default=0, nullable=False)
    last_reminder_sent_at = db.Column(db.DateTime, nullable=True)
    last_reminder_error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    assigned_to = db.relationship('User', backref='tasks')
    donor = db.relationship('Donor', backref='tasks')
    reminders = db.relationship('TaskReminder', backref='task', cascade='all, delete-orphan', order_by='TaskReminder.sent_at')

    def __repr__(self):
        return f'<Task {self.title[:40]} [{self.status}]>'


class TaskReminder(db.Model):
    """Immutable history of task reminders sent to assignee."""

    __tablename__ = 'task_reminders'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    sent_to_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Reminder delivery
    channel = db.Column(db.String(20), nullable=False)  # email, sms, in_app
    recipient_email = db.Column(db.String(255), nullable=True)  # snapshot at send time
    recipient_phone = db.Column(db.String(30), nullable=True)  # snapshot at send time
    
    # Reminder type/timing
    reminder_type = db.Column(db.String(30), default='upcoming', nullable=False)  # upcoming (before due), overdue, escalation
    sent_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    delivery_status = db.Column(db.String(30), default='pending', nullable=False)  # pending, sent, failed, bounced
    delivery_error = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    
    sent_to_user = db.relationship('User', backref='task_reminders')
    
    def __repr__(self):
        return f'<TaskReminder task_id={self.task_id} {self.channel} [{self.delivery_status}]>'


class ProjectMilestone(db.Model):
    """Project-level milestone used for board and delivery tracking."""

    __tablename__ = 'project_milestones'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.DateTime, nullable=True, index=True)
    status = db.Column(db.String(20), default='planned', nullable=False, index=True)  # planned, in_progress, completed, blocked
    owner_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    project = db.relationship('Project', backref='milestones')
    owner = db.relationship('User', backref='owned_project_milestones')

    def __repr__(self):
        return f'<ProjectMilestone project={self.project_id} title={self.title} status={self.status}>'


class TaskDependency(db.Model):
    """Directed dependency edge where a task depends on another task."""

    __tablename__ = 'task_dependencies'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False, index=True)
    depends_on_task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False, index=True)
    dependency_type = db.Column(db.String(20), default='blocks', nullable=False)  # blocks, related
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    task = db.relationship('Task', foreign_keys=[task_id], backref='dependencies')
    depends_on_task = db.relationship('Task', foreign_keys=[depends_on_task_id], backref='dependent_tasks')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'task_id', 'depends_on_task_id', name='uq_task_dependency_org_task_depends_on'),
    )

    def __repr__(self):
        return f'<TaskDependency task={self.task_id} depends_on={self.depends_on_task_id}>'


# ---------------------------------------------------------------------------
# Program / Impact Case Tracking
# ---------------------------------------------------------------------------

class ProgramCase(db.Model):
    """Flexible case tracking for beneficiary services, grant deliverables, or advocacy."""

    __tablename__ = 'program_cases'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=True, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True, index=True)  # supporter link
    beneficiary_id = db.Column(db.Integer, db.ForeignKey('beneficiaries.id'), nullable=True, index=True)

    title = db.Column(db.String(300), nullable=False)
    case_type = db.Column(db.String(50), default='service', nullable=False)  # service, grant_deliverable, advocacy, beneficiary
    status = db.Column(db.String(50), default='open', nullable=False, index=True)  # open, in_progress, on_hold, closed
    priority = db.Column(db.String(20), default='medium', nullable=False)
    description = db.Column(db.Text, nullable=True)
    outcome = db.Column(db.Text, nullable=True)       # measured outcome / impact statement
    outcome_metric = db.Column(db.String(200), nullable=True)  # e.g. "Families housed"
    outcome_value = db.Column(db.Float, nullable=True)          # e.g. 12
    target_outcome_value = db.Column(db.Float, nullable=True)
    progress_percent = db.Column(db.Float, default=0.0, nullable=False)

    intake_stage = db.Column(db.String(32), default='intake', nullable=False)  # intake, assessment, active_service, follow_up, closed
    risk_level = db.Column(db.String(20), nullable=True)  # low, medium, high, critical
    intake_summary = db.Column(db.Text, nullable=True)

    opened_date = db.Column(db.Date, nullable=True)
    closed_date = db.Column(db.Date, nullable=True)
    next_review_date = db.Column(db.Date, nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    activities = db.relationship('CaseActivity', backref='case', cascade='all, delete-orphan', order_by='CaseActivity.created_at')
    service_logs = db.relationship('BeneficiaryServiceLog', backref='case', cascade='all, delete-orphan', order_by='BeneficiaryServiceLog.service_date')
    outcome_metrics = db.relationship('CaseOutcomeMetric', backref='case', cascade='all, delete-orphan', order_by='CaseOutcomeMetric.recorded_at')
    organization = db.relationship('Organization', backref='program_cases')

    def __repr__(self):
        return f'<ProgramCase {self.title[:40]} [{self.status}]>'


class CaseActivity(db.Model):
    """Immutable activity/note on a program case (every status change recorded)."""

    __tablename__ = 'case_activities'

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    activity_type = db.Column(db.String(50), nullable=False)  # note, status_change, document, call, email
    content = db.Column(db.Text, nullable=True)
    previous_status = db.Column(db.String(50), nullable=True)
    new_status = db.Column(db.String(50), nullable=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    actor = db.relationship('User', backref='case_activities')

    def __repr__(self):
        return f'<CaseActivity case={self.case_id} {self.activity_type}>'


class BeneficiaryServiceLog(db.Model):
    """Structured service delivery entry tied to a beneficiary case."""

    __tablename__ = 'beneficiary_service_logs'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    beneficiary_id = db.Column(db.Integer, db.ForeignKey('beneficiaries.id'), nullable=True, index=True)
    staff_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    service_type = db.Column(db.String(80), nullable=False)
    service_date = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)
    duration_minutes = db.Column(db.Integer, nullable=True)
    service_units = db.Column(db.Float, nullable=True)
    outcome_note = db.Column(db.Text, nullable=True)
    metadata_json = db.Column('metadata', JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    staff_user = db.relationship('User', backref='beneficiary_service_logs')
    beneficiary = db.relationship('Beneficiary', backref='service_logs')

    def __repr__(self):
        return f'<BeneficiaryServiceLog case={self.case_id} {self.service_type}>'


class CaseOutcomeMetric(db.Model):
    """Time-series metric updates for case-level outcome tracking."""

    __tablename__ = 'case_outcome_metrics'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    metric_name = db.Column(db.String(120), nullable=False)
    unit = db.Column(db.String(40), nullable=True)
    baseline_value = db.Column(db.Float, nullable=True)
    target_value = db.Column(db.Float, nullable=True)
    current_value = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)
    note = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<CaseOutcomeMetric case={self.case_id} {self.metric_name}={self.current_value}>'


class ProgramCaseGoal(db.Model):
    """Goal/milestone target attached to a program case."""

    __tablename__ = 'program_case_goals'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    metric_name = db.Column(db.String(120), nullable=True)
    target_value = db.Column(db.Float, nullable=True)
    current_value = db.Column(db.Float, nullable=True)
    unit = db.Column(db.String(40), nullable=True)
    status = db.Column(db.String(30), default='planned', nullable=False, index=True)  # planned, in_progress, achieved, blocked, cancelled
    target_date = db.Column(db.Date, nullable=True, index=True)
    achieved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    case = db.relationship('ProgramCase', backref='goals')

    def __repr__(self):
        return f'<ProgramCaseGoal case={self.case_id} {self.title[:30]} [{self.status}]>'


class ProgramCaseTask(db.Model):
    """Concrete action item for progressing a case goal."""

    __tablename__ = 'program_case_tasks'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    goal_id = db.Column(db.Integer, db.ForeignKey('program_case_goals.id'), nullable=True, index=True)
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default='todo', nullable=False, index=True)  # todo, in_progress, done, blocked, cancelled
    priority = db.Column(db.String(20), default='medium', nullable=False)  # low, medium, high, urgent
    due_date = db.Column(db.Date, nullable=True, index=True)
    is_milestone = db.Column(db.Boolean, default=False, nullable=False, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    case = db.relationship('ProgramCase', backref='tasks')
    goal = db.relationship('ProgramCaseGoal', backref='tasks')
    assigned_to = db.relationship('User', backref='program_case_tasks')

    def __repr__(self):
        return f'<ProgramCaseTask case={self.case_id} {self.title[:30]} [{self.status}]>'


class ProgramCaseDocument(db.Model):
    """Document metadata linked to a case (attachments, consent forms, plans)."""

    __tablename__ = 'program_case_documents'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    category = db.Column(db.String(50), default='attachment', nullable=False, index=True)  # attachment, consent, assessment, plan, referral, evidence
    title = db.Column(db.String(300), nullable=False)
    file_name = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(120), nullable=True)
    storage_key = db.Column(db.String(500), nullable=True)  # object key/path in storage backend
    external_url = db.Column(db.String(1000), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    case = db.relationship('ProgramCase', backref='documents')
    uploaded_by = db.relationship('User', backref='program_case_documents')

    def __repr__(self):
        return f'<ProgramCaseDocument case={self.case_id} {self.title[:30]}>'


class ProgramCaseFollowUp(db.Model):
    """Follow-up workflow item with reminder and escalation metadata."""

    __tablename__ = 'program_case_followups'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    beneficiary_id = db.Column(db.Integer, db.ForeignKey('beneficiaries.id'), nullable=True, index=True)
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default='scheduled', nullable=False, index=True)  # scheduled, in_progress, completed, missed, escalated, cancelled
    follow_up_type = db.Column(db.String(50), default='general', nullable=False)
    due_at = db.Column(db.DateTime, nullable=False, index=True)
    reminder_at = db.Column(db.DateTime, nullable=True, index=True)
    reminder_channel = db.Column(db.String(20), default='auto', nullable=False)  # auto, email, sms
    reminder_sent_count = db.Column(db.Integer, default=0, nullable=False)
    last_reminder_sent_at = db.Column(db.DateTime, nullable=True)
    last_reminder_error = db.Column(db.Text, nullable=True)
    escalation_level = db.Column(db.Integer, default=0, nullable=False)
    escalation_reason = db.Column(db.Text, nullable=True)
    escalated_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    case = db.relationship('ProgramCase', backref='followups')
    beneficiary = db.relationship('Beneficiary', backref='followups')
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_user_id], backref='program_case_followups_assigned')
    created_by = db.relationship('User', foreign_keys=[created_by_user_id], backref='program_case_followups_created')

    def __repr__(self):
        return f'<ProgramCaseFollowUp case={self.case_id} {self.title[:30]} [{self.status}]>'


# ---------------------------------------------------------------------------
# Engagement Scoring
# ---------------------------------------------------------------------------

class DonorEngagementScore(db.Model):
    """Computed engagement health score for a donor (0–100), broken down by dimension."""

    __tablename__ = 'donor_engagement_scores'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=False, index=True)

    score = db.Column(db.Float, nullable=False, default=0.0)   # 0–100 composite

    # Dimension breakdown
    recency_score = db.Column(db.Float, default=0.0)    # 0–25
    frequency_score = db.Column(db.Float, default=0.0)  # 0–25
    monetary_score = db.Column(db.Float, default=0.0)   # 0–25
    engagement_score = db.Column(db.Float, default=0.0) # 0–25 (membership, events, tasks)

    # Human-readable explanation
    explanation = db.Column(db.Text, nullable=True)
    segment = db.Column(db.String(50), nullable=True)   # champion, loyal, at_risk, lapsed, new
    cultivation_priority = db.Column(db.String(20), default='medium', nullable=False)  # low, medium, high

    computed_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)

    donor = db.relationship('Donor', backref=db.backref('engagement_score', uselist=False))

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'donor_id', name='uq_engagement_score_org_donor'),
    )

    def __repr__(self):
        return f'<DonorEngagementScore donor={self.donor_id} score={self.score:.1f}>'


# ---------------------------------------------------------------------------
# Smart Groups / Dynamic Audiences
# ---------------------------------------------------------------------------

class SmartGroup(db.Model):
    """Saved rules-based audience that auto-evaluates against live data."""

    __tablename__ = 'smart_groups'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    rules_json = db.Column(JSON, nullable=False)  # serialized list of rule dicts
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_evaluated_at = db.Column(db.DateTime, nullable=True)
    last_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    organization = db.relationship('Organization', backref='smart_groups')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'name', name='uq_smart_group_org_name'),
    )

    def __repr__(self):
        return f'<SmartGroup {self.name}>'


# ---------------------------------------------------------------------------
# P2P Fundraising (ORM-backed)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

class Campaign(db.Model):
    """Fundraising campaign (annual, capital, event, emergency, P2P umbrella, etc.)."""

    __tablename__ = 'campaigns'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    fund_id = db.Column(db.Integer, db.ForeignKey('funds.id'), nullable=True, index=True)

    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(120), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    campaign_type = db.Column(
        db.String(30),
        default='general',
        nullable=False,
    )  # annual, capital, event, emergency, recurring, p2p, general
    status = db.Column(db.String(20), default='draft', nullable=False, index=True)  # draft, active, paused, closed
    goal_amount = db.Column(db.Float, nullable=False, default=0.0)
    raised_amount = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    photo_path = db.Column(db.String(300), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    organization = db.relationship('Organization', backref='campaigns')
    fund = db.relationship('Fund', backref='campaigns')
    p2p_pages = db.relationship('P2PPage', backref='campaign', foreign_keys='P2PPage.campaign_id')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'slug', name='uq_campaign_org_slug'),
    )

    def __repr__(self):
        return f'<Campaign {self.name} [{self.status}]>'


class CampaignEmailBatch(db.Model):
    """Bulk email send operation for a campaign audience."""

    __tablename__ = 'campaign_email_batches'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    subject = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, nullable=False)
    audience_json = db.Column(JSON, nullable=True)
    status = db.Column(db.String(30), nullable=False, default='queued', index=True)  # queued, sent, partial_failed, failed
    total_recipients = db.Column(db.Integer, nullable=False, default=0)
    sent_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=True)
    scheduled_at = db.Column(db.DateTime, nullable=True)

    campaign = db.relationship('Campaign', backref='email_batches')
    created_by = db.relationship('User', backref='campaign_email_batches')
    deliveries = db.relationship(
        'CampaignEmailDelivery',
        backref='batch',
        cascade='all, delete-orphan',
        order_by='CampaignEmailDelivery.id',
    )

    def __repr__(self):
        return f'<CampaignEmailBatch campaign={self.campaign_id} status={self.status}>'


class CampaignEmailDelivery(db.Model):
    """Per-recipient delivery tracking for campaign email sends."""

    __tablename__ = 'campaign_email_deliveries'

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('campaign_email_batches.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True, index=True)
    recipient_email = db.Column(db.String(255), nullable=False, index=True)
    delivery_status = db.Column(db.String(30), nullable=False, default='pending', index=True)  # sent, failed
    error_message = db.Column(db.Text, nullable=True)
    open_count = db.Column(db.Integer, nullable=False, default=0)
    click_count = db.Column(db.Integer, nullable=False, default=0)
    last_opened_at = db.Column(db.DateTime, nullable=True)
    last_clicked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=True)

    campaign = db.relationship('Campaign', backref='email_deliveries')
    donor = db.relationship('Donor', backref='campaign_email_deliveries')

    def __repr__(self):
        return f'<CampaignEmailDelivery batch={self.batch_id} {self.delivery_status}>'


class CampaignEmailOptOut(db.Model):
    """Donor email opt-out (unsubscribe) record for campaign emails."""

    __tablename__ = 'campaign_email_opt_outs'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    token = db.Column(db.String(64), nullable=False, unique=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=True, index=True)
    unsubscribed_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    donor = db.relationship('Donor', backref='email_opt_outs')

    def __repr__(self):
        return f'<CampaignEmailOptOut email={self.email}>'


class CampaignCommunicationPreference(db.Model):
    """Communication preferences used by campaign/newsletter preference center."""

    __tablename__ = 'campaign_communication_preferences'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    newsletter_opt_in = db.Column(db.Boolean, nullable=False, default=True)
    campaign_opt_in = db.Column(db.Boolean, nullable=False, default=True)
    events_opt_in = db.Column(db.Boolean, nullable=False, default=True)
    volunteer_opt_in = db.Column(db.Boolean, nullable=False, default=True)
    digest_frequency = db.Column(db.String(20), nullable=False, default='weekly')
    source = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    organization = db.relationship('Organization', backref='campaign_communication_preferences')
    donor = db.relationship('Donor', backref='campaign_communication_preferences')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'email', name='uq_campaign_comm_pref_org_email'),
    )

    def __repr__(self):
        return f'<CampaignCommunicationPreference email={self.email}>'


class EventDiscountCode(db.Model):
    """Event-scoped discount code for registration/payment checkout flows."""

    __tablename__ = 'event_discount_codes'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, nullable=False, index=True)
    code = db.Column(db.String(64), nullable=False, index=True)
    discount_type = db.Column(db.String(20), nullable=False)  # percentage, fixed
    discount_value = db.Column(db.Float, nullable=False)
    usage_limit = db.Column(db.Integer, nullable=True)
    usage_count = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    __table_args__ = (
        db.UniqueConstraint('event_id', 'code', name='uq_event_discount_codes_event_code'),
    )

    def __repr__(self):
        return f'<EventDiscountCode event={self.event_id} code={self.code}>'


class ExternalCommunicationAuthorization(db.Model):
    """Human authorization audit record for outbound external communications."""

    __tablename__ = 'external_communication_authorizations'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    username = db.Column(db.String(120), nullable=False)
    user_role = db.Column(db.String(32), nullable=False)
    channel = db.Column(db.String(40), nullable=False, index=True)  # email, sms, webhook, etc.
    communication_type = db.Column(db.String(80), nullable=False, index=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=True, index=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('campaign_email_batches.id'), nullable=True, index=True)
    warning_acknowledged = db.Column(db.Boolean, nullable=False, default=False)
    confirmation_phrase = db.Column(db.String(80), nullable=False)
    reviewer_name = db.Column(db.String(180), nullable=False)
    reviewer_role = db.Column(db.String(120), nullable=True)
    details_json = db.Column(JSON, nullable=True)
    authorized_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)

    organization = db.relationship('Organization', backref='external_communication_authorizations')
    user = db.relationship('User', backref='external_communication_authorizations')
    campaign = db.relationship('Campaign', backref='external_communication_authorizations')

    def __repr__(self):
        return f'<ExternalCommunicationAuthorization channel={self.channel} user={self.user_id}>'


# ---------------------------------------------------------------------------
# P2P Fundraising (ORM-backed)
# ---------------------------------------------------------------------------

class P2PPage(db.Model):
    """Supporter-created peer-to-peer fundraising page."""

    __tablename__ = 'p2p_pages'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=False, index=True)  # page owner
    campaign_slug = db.Column(db.String(120), nullable=True, index=True)  # optional parent campaign
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=True, index=True)

    title = db.Column(db.String(300), nullable=False)
    story = db.Column(db.Text, nullable=True)
    goal_amount = db.Column(db.Float, nullable=False, default=0.0)
    match_ratio = db.Column(db.Float, nullable=False, default=0.0)  # e.g. 1.0 = 1:1 match
    match_cap_amount = db.Column(db.Float, nullable=False, default=0.0)
    challenge_goal_amount = db.Column(db.Float, nullable=False, default=0.0)
    challenge_end_date = db.Column(db.Date, nullable=True)
    automation_contact_email = db.Column(db.String(255), nullable=True)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    status = db.Column(db.String(20), default='active', nullable=False, index=True)  # draft, active, closed
    public_slug = db.Column(db.String(80), unique=True, nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    donations = db.relationship('Donation', secondary='p2p_page_donations', backref='p2p_pages')
    owner = db.relationship('Donor', backref='p2p_pages')

    def __repr__(self):
        return f'<P2PPage {self.title[:40]} [{self.status}]>'


class P2PPageDonation(db.Model):
    """Association table linking P2P pages to donations."""

    __tablename__ = 'p2p_page_donations'

    page_id = db.Column(db.Integer, db.ForeignKey('p2p_pages.id'), primary_key=True)
    donation_id = db.Column(db.Integer, db.ForeignKey('donations.id'), primary_key=True)


# ---------------------------------------------------------------------------
# Assessment, Referral & Appointment models
# ---------------------------------------------------------------------------

class BeneficiaryAssessment(db.Model):
    """Structured needs/risk assessment linked to a program case."""

    __tablename__ = 'beneficiary_assessments'

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    assessor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    assessment_type = db.Column(db.String(50), default='initial', nullable=False)  # initial, follow_up, exit
    assessment_date = db.Column(db.Date, nullable=False)

    # Core domain scores (0-10 each; nullable means not assessed)
    housing_score = db.Column(db.Float, nullable=True)
    food_security_score = db.Column(db.Float, nullable=True)
    health_score = db.Column(db.Float, nullable=True)
    employment_score = db.Column(db.Float, nullable=True)
    safety_score = db.Column(db.Float, nullable=True)
    education_score = db.Column(db.Float, nullable=True)

    # Aggregate
    total_score = db.Column(db.Float, nullable=True)
    risk_level = db.Column(db.String(20), default='medium', nullable=False)  # low, medium, high, critical

    # Freeform additional domains as JSON
    extra_domains = db.Column(JSON, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    case = db.relationship('ProgramCase', backref='assessments')
    assessor = db.relationship('User', backref='assessments_conducted')

    def __repr__(self):
        return f'<BeneficiaryAssessment case={self.case_id} type={self.assessment_type} risk={self.risk_level}>'


class BeneficiaryReferral(db.Model):
    """Referral from a case to an external or internal provider."""

    __tablename__ = 'beneficiary_referrals'

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    referred_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    referral_type = db.Column(db.String(30), default='external', nullable=False)  # external, internal
    provider_name = db.Column(db.String(200), nullable=False)
    provider_contact = db.Column(db.String(200), nullable=True)
    provider_email = db.Column(db.String(120), nullable=True)
    provider_phone = db.Column(db.String(20), nullable=True)
    service_type = db.Column(db.String(100), nullable=True)  # housing, mental_health, employment, food, legal, other

    referral_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(30), default='pending', nullable=False)  # pending, accepted, declined, completed, no_show
    outcome_date = db.Column(db.Date, nullable=True)
    outcome_notes = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    case = db.relationship('ProgramCase', backref='referrals')
    referred_by = db.relationship('User', backref='referrals_made')

    def __repr__(self):
        return f'<BeneficiaryReferral case={self.case_id} provider={self.provider_name} [{self.status}]>'


class BeneficiaryAppointment(db.Model):
    """Scheduled appointment for a beneficiary / case worker session."""

    __tablename__ = 'beneficiary_appointments'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=True, index=True)
    beneficiary_id = db.Column(db.Integer, db.ForeignKey('beneficiaries.id'), nullable=True, index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    title = db.Column(db.String(200), nullable=False)
    appointment_type = db.Column(
        db.String(50), default='case_review', nullable=False
    )  # intake, case_review, follow_up, exit_interview, service_delivery
    scheduled_at = db.Column(db.DateTime, nullable=False, index=True)
    duration_minutes = db.Column(db.Integer, default=60, nullable=False)
    location = db.Column(db.String(200), nullable=True)
    is_virtual = db.Column(db.Boolean, default=False, nullable=False)
    meeting_link = db.Column(db.String(500), nullable=True)

    status = db.Column(db.String(20), default='scheduled', nullable=False)  # scheduled, confirmed, completed, cancelled, no_show
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    case = db.relationship('ProgramCase', backref='appointments')
    beneficiary = db.relationship('Beneficiary', backref='appointments')
    staff = db.relationship('User', backref='appointments_assigned')

    def __repr__(self):
        return f'<BeneficiaryAppointment {self.title} [{self.status}]>'


# ---------------------------------------------------------------------------
# Scheduled Reports
# ---------------------------------------------------------------------------

class ScheduledReport(db.Model):
    """Configured scheduled report that runs on a cron-like interval."""

    __tablename__ = 'scheduled_reports'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    name = db.Column(db.String(200), nullable=False)
    report_type = db.Column(db.String(50), nullable=False)  # funder, impact, donations, donors, expenses
    frequency = db.Column(db.String(20), default='monthly', nullable=False)  # daily, weekly, monthly, quarterly
    delivery_email = db.Column(db.String(120), nullable=True)
    parameters = db.Column(JSON, nullable=True)  # e.g., {"funder_name": "Ford Foundation", "currency": "USD"}
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_run_at = db.Column(db.DateTime, nullable=True)
    next_run_at = db.Column(db.DateTime, nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    created_by = db.relationship('User', backref='scheduled_reports')

    def __repr__(self):
        return f'<ScheduledReport {self.name} [{self.frequency}]>'


class CollaborationChannel(db.Model):
    """A collaboration channel for team threads or direct messages."""

    __tablename__ = 'collaboration_channels'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    channel_type = db.Column(db.String(20), default='team', nullable=False, index=True)  # team, direct
    name = db.Column(db.String(200), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    is_archived = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    created_by = db.relationship('User', backref='created_collaboration_channels')

    def __repr__(self):
        return f'<CollaborationChannel id={self.id} type={self.channel_type}>'


class CollaborationChannelMember(db.Model):
    """Membership row linking users to collaboration channels."""

    __tablename__ = 'collaboration_channel_members'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('collaboration_channels.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    role = db.Column(db.String(20), default='member', nullable=False)  # owner, member
    joined_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    last_read_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    channel = db.relationship('CollaborationChannel', backref='memberships')
    user = db.relationship('User', backref='collaboration_memberships')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'channel_id', 'user_id', name='uq_collab_channel_member_org_channel_user'),
    )

    def __repr__(self):
        return f'<CollaborationChannelMember channel={self.channel_id} user={self.user_id}>'


class CollaborationMessage(db.Model):
    """Message posted to a collaboration channel."""

    __tablename__ = 'collaboration_messages'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('collaboration_channels.id'), nullable=False, index=True)
    sender_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)
    edited_at = db.Column(db.DateTime, nullable=True)

    channel = db.relationship('CollaborationChannel', backref='messages')
    sender = db.relationship('User', backref='sent_collaboration_messages')

    def __repr__(self):
        return f'<CollaborationMessage channel={self.channel_id} sender={self.sender_user_id}>'


class CollaborationPresence(db.Model):
    """Latest presence snapshot for an organization user."""

    __tablename__ = 'collaboration_presence'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    status = db.Column(db.String(20), default='offline', nullable=False, index=True)  # online, away, dnd, offline
    status_message = db.Column(db.String(300), nullable=True)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    user = db.relationship('User', backref='presence_rows')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'user_id', name='uq_collab_presence_org_user'),
    )

    def __repr__(self):
        return f'<CollaborationPresence user={self.user_id} status={self.status}>'


# Export all models
class VolunteerShift(db.Model):
    """A scheduled or completed volunteer shift."""

    __tablename__ = 'volunteer_shifts'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    volunteer_id = db.Column(db.Integer, db.ForeignKey('volunteers.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    shift_date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.String(5), nullable=True)   # "HH:MM" 24h
    end_time = db.Column(db.String(5), nullable=True)
    hours = db.Column(db.Float, nullable=True)
    location = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(30), default='scheduled', nullable=False)
    # scheduled / confirmed / completed / cancelled / no_show
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    volunteer = db.relationship('Volunteer', backref='shifts')
    project = db.relationship('Project', backref='volunteer_shifts')

    def __repr__(self):
        return f'<VolunteerShift {self.id} {self.title}>'


class TrainingCourse(db.Model):
    """A training course definition available for volunteers."""

    __tablename__ = 'training_courses'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(50), default='orientation', nullable=False)
    # orientation / safety / skills / compliance
    duration_hours = db.Column(db.Float, nullable=True)
    is_required = db.Column(db.Boolean, default=False, nullable=False)
    expires_after_days = db.Column(db.Integer, nullable=True)  # None = no expiry
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self):
        return f'<TrainingCourse {self.name}>'


class VolunteerTraining(db.Model):
    """Assignment and completion record for a volunteer on a training course."""

    __tablename__ = 'volunteer_trainings'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    volunteer_id = db.Column(db.Integer, db.ForeignKey('volunteers.id'), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey('training_courses.id'), nullable=False, index=True)
    assigned_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), default='pending', nullable=False)
    # pending / in_progress / completed / expired
    score = db.Column(db.Float, nullable=True)  # 0-100
    expires_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    volunteer = db.relationship('Volunteer', backref='trainings')
    course = db.relationship('TrainingCourse', backref='assignments')

    def __repr__(self):
        return f'<VolunteerTraining v={self.volunteer_id} c={self.course_id}>'


class AccountingSyncLog(db.Model):
    """Audit trail of records pushed to external accounting systems."""

    __tablename__ = 'accounting_sync_logs'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    provider = db.Column(db.String(30), nullable=False, index=True)  # quickbooks / xero
    sync_type = db.Column(db.String(30), nullable=False)             # donation / expense / fund / contact
    internal_id = db.Column(db.Integer, nullable=True, index=True)   # FK to local record
    external_id = db.Column(db.String(100), nullable=True)           # ID in remote system
    external_ref = db.Column(db.String(200), nullable=True)          # human-readable ref e.g. INV-123
    status = db.Column(db.String(20), default='pending', nullable=False, index=True)
    # pending / synced / failed / skipped
    error_message = db.Column(db.Text, nullable=True)
    synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    def __repr__(self):
        return f'<AccountingSyncLog {self.provider} {self.sync_type} {self.status}>'


__all__ = [
    'db',
    'User',
    'Organization',
    'Donor',
    'Donation',
    'RecurringDonationPlan',
    'DonationReceipt',
    'Project',
    'Fund',
    'Volunteer',
    'Expense',
    'Beneficiary',
    'AIConversation',
    'AIMessage',
    'Grant',
    'GrantDisbursement',
    'GrantBudgetLine',
    'GrantExpenseAllocation',
    'GrantOpportunity',
    'GrantProposal',
    'GrantSearchProfile',
    'GrantSearchAlert',
    'GrantOutcomeTemplate',
    'GrantOutcomeRecord',
    'GrantApprovalRequest',
    'GrantApprovalDecision',
    'GrantApprovalChainConfig',
    'MembershipTier',
    'MembershipRecord',
    'StewardshipJourney',
    'StewardshipStep',
    'StewardshipEnrollment',
    'DonorJourneyAutomationEvent',
    'FormSubmissionEvent',
    'Task',
    'TaskReminder',
    'ProjectMilestone',
    'TaskDependency',
    'ProgramCase',
    'CaseActivity',
    'BeneficiaryServiceLog',
    'CaseOutcomeMetric',
    'ProgramCaseGoal',
    'ProgramCaseTask',
    'ProgramCaseDocument',
    'ProgramCaseFollowUp',
    'DonorEngagementScore',
    'SmartGroup',
    'Campaign',
    'CampaignEmailBatch',
    'CampaignEmailDelivery',
    'CampaignEmailOptOut',
    'CampaignCommunicationPreference',
    'ExternalCommunicationAuthorization',
    'EventDiscountCode',
    'P2PPage',
    'P2PPageDonation',
    'BeneficiaryAssessment',
    'BeneficiaryReferral',
    'BeneficiaryAppointment',
    'ScheduledReport',
    'CollaborationChannel',
    'CollaborationChannelMember',
    'CollaborationMessage',
    'CollaborationPresence',
    'VolunteerShift',
    'TrainingCourse',
    'VolunteerTraining',
    'AccountingSyncLog',
]
