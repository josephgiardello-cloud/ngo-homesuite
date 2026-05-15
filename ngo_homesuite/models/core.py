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


class Grant(db.Model):
    """Grant opportunity tracked through full lifecycle (prospect → awarded → disbursed)."""

    __tablename__ = 'grants'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)

    # Funder details
    funder_name = db.Column(db.String(200), nullable=False)
    funder_type = db.Column(db.String(50), default='foundation', nullable=False)  # foundation, government, corporate, other
    funder_contact = db.Column(db.String(200), nullable=True)
    funder_email = db.Column(db.String(120), nullable=True)

    # Grant details
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    amount_requested = db.Column(db.Float, nullable=True)
    amount_awarded = db.Column(db.Float, nullable=True)
    currency = db.Column(db.String(3), default='USD', nullable=False)

    # Dates
    application_deadline = db.Column(db.Date, nullable=True, index=True)
    submission_date = db.Column(db.Date, nullable=True)
    award_date = db.Column(db.Date, nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    report_due_date = db.Column(db.Date, nullable=True)

    # Status lifecycle
    status = db.Column(
        db.String(50), default='prospect', nullable=False, index=True
    )  # prospect, in_progress, submitted, awarded, declined, closed, reporting

    # Reporting
    requirements = db.Column(db.Text, nullable=True)  # reporting requirements
    notes = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    disbursements = db.relationship('GrantDisbursement', backref='grant', cascade='all, delete-orphan')
    project = db.relationship('Project', backref='grants')
    organization = db.relationship('Organization', backref='grants')

    def __repr__(self):
        return f'<Grant {self.title} [{self.status}]>'


class GrantDisbursement(db.Model):
    """Individual payment received from a grant award."""

    __tablename__ = 'grant_disbursements'

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    received_date = db.Column(db.Date, nullable=False)
    reference = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    def __repr__(self):
        return f'<GrantDisbursement grant={self.grant_id} {self.amount}>'


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

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    assigned_to = db.relationship('User', backref='tasks')
    donor = db.relationship('Donor', backref='tasks')

    def __repr__(self):
        return f'<Task {self.title[:40]} [{self.status}]>'


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

    opened_date = db.Column(db.Date, nullable=True)
    closed_date = db.Column(db.Date, nullable=True)
    next_review_date = db.Column(db.Date, nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    activities = db.relationship('CaseActivity', backref='case', cascade='all, delete-orphan', order_by='CaseActivity.created_at')
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

class P2PPage(db.Model):
    """Supporter-created peer-to-peer fundraising page."""

    __tablename__ = 'p2p_pages'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('donors.id'), nullable=False, index=True)  # page owner
    campaign_slug = db.Column(db.String(120), nullable=True, index=True)  # optional parent campaign

    title = db.Column(db.String(300), nullable=False)
    story = db.Column(db.Text, nullable=True)
    goal_amount = db.Column(db.Float, nullable=False, default=0.0)
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
    'Grant',
    'GrantDisbursement',
    'MembershipTier',
    'MembershipRecord',
    'StewardshipJourney',
    'StewardshipStep',
    'StewardshipEnrollment',
    'Task',
    'ProgramCase',
    'CaseActivity',
    'DonorEngagementScore',
    'SmartGroup',
    'P2PPage',
    'P2PPageDonation',
]
