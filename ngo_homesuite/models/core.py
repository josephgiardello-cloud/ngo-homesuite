"""
Flask-SQLAlchemy models for NGO HomeSuite.

Core entities:
- User: System users with roles (admin, fundraiser, volunteer_manager, viewer)
- Organization: NGO organization/tenant
- Beneficiary: Individual benefiting from the organization
- Project: Initiative/project within the organization
- Donation: Financial contribution to the organization
"""

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy.dialects.sqlite import JSON

db = SQLAlchemy()
password_hasher = PasswordHasher()

def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)



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
    
    # Organization association
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=True)
    organization = db.relationship('Organization', backref='users')
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)
    last_login = db.Column(db.DateTime, nullable=True)
    
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
    fund_id = db.Column(db.Integer, db.ForeignKey('funds.id'), nullable=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=True)
    
    # Donor info
    donor_name = db.Column(db.String(200), nullable=False)
    donor_email = db.Column(db.String(120), nullable=True)
    donor_phone = db.Column(db.String(20), nullable=True)
    
    # Donation details
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    donation_date = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)
    
    # Payment info
    payment_method = db.Column(db.String(50), nullable=True)  # e.g., 'credit_card', 'bank_transfer', 'cash'
    reference_number = db.Column(db.String(100), nullable=True, unique=True)
    
    # Status
    status = db.Column(db.String(50), default='received', nullable=False)  # received, processed, receipted
    
    # Purpose
    purpose = db.Column(db.String(200), nullable=True)  # e.g., 'General Fund', 'Emergency Relief', 'Specific Project'
    notes = db.Column(db.Text, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)
    
    def __repr__(self):
        return f'<Donation {self.amount} {self.currency}>'


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
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    donor = db.relationship('Donor', backref='recurring_plans')

    def __repr__(self):
        return f'<RecurringDonationPlan donor={self.donor_id} {self.frequency} {self.amount} {self.currency}>'


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


class Donor(db.Model):
    """Donor profile and contact information."""

    __tablename__ = 'donors'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    email = db.Column(db.String(120), nullable=True, index=True)
    phone = db.Column(db.String(20), nullable=True)
    donor_type = db.Column(db.String(50), default='individual', nullable=False)
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
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    donations = db.relationship('Donation', backref='fund')
    expenses = db.relationship('Expense', backref='fund')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'name', name='uq_funds_org_name'),
    )

    def __repr__(self):
        return f'<Fund {self.name}>'


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


# Export all models
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
]
