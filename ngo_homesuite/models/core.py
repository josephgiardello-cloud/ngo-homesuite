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

    # Account lockout after repeated failed logins
    failed_login_count = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    
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
    version_id = db.Column(db.Integer, nullable=False, default=0)
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
    budget_lines = db.relationship('GrantBudgetLine', backref='grant', cascade='all, delete-orphan')
    expense_allocations = db.relationship('GrantExpenseAllocation', backref='grant', cascade='all, delete-orphan')
    opportunities = db.relationship('GrantOpportunity', backref='awarded_grant', foreign_keys='GrantOpportunity.awarded_grant_id')
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


class GrantBudgetLine(db.Model):
    """Line-item budget allocation within a grant award."""

    __tablename__ = 'grant_budget_lines'

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    category = db.Column(db.String(80), nullable=False, index=True)
    line_name = db.Column(db.String(200), nullable=False)
    allocated_amount = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    allocations = db.relationship('GrantExpenseAllocation', backref='budget_line', cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('grant_id', 'category', name='uq_grant_budget_line_grant_category'),
    )

    __mapper_args__ = {
        'version_id_col': version_id,
    }

    def __repr__(self):
        return f'<GrantBudgetLine grant={self.grant_id} category={self.category}>'


class GrantExpenseAllocation(db.Model):
    """Expense-to-grant line allocation for restricted fund tracking."""

    __tablename__ = 'grant_expense_allocations'

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=False, index=True)
    budget_line_id = db.Column(db.Integer, db.ForeignKey('grant_budget_lines.id'), nullable=False, index=True)
    expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id'), nullable=False, unique=True, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    supporting_document_ref = db.Column(db.String(255), nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    expense = db.relationship('Expense', backref='grant_allocations')

    __mapper_args__ = {
        'version_id_col': version_id,
    }

    def __repr__(self):
        return f'<GrantExpenseAllocation grant={self.grant_id} expense={self.expense_id} amount={self.amount}>'


class GrantOpportunity(db.Model):
    """Pre-award grant opportunity tracking and forecast metadata."""

    __tablename__ = 'grant_opportunities'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    awarded_grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=True, index=True)
    funder_name = db.Column(db.String(200), nullable=False)
    program_name = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    deadline = db.Column(db.Date, nullable=True, index=True)
    amount_min = db.Column(db.Float, nullable=True)
    amount_max = db.Column(db.Float, nullable=True)
    probability = db.Column(db.Float, nullable=False, default=0.0)
    probability_weighted_amount = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(30), nullable=False, default='identified', index=True)
    notes = db.Column(db.Text, nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    proposals = db.relationship('GrantProposal', backref='opportunity', cascade='all, delete-orphan')

    __mapper_args__ = {
        'version_id_col': version_id,
    }

    def __repr__(self):
        return f'<GrantOpportunity {self.title} [{self.status}]>'


class GrantProposal(db.Model):
    """Versioned proposal record linked to a grant opportunity."""

    __tablename__ = 'grant_proposals'

    id = db.Column(db.Integer, primary_key=True)
    opportunity_id = db.Column(db.Integer, db.ForeignKey('grant_opportunities.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    version_number = db.Column(db.Integer, nullable=False, default=1)
    amount_requested = db.Column(db.Float, nullable=True)
    narrative_summary = db.Column(db.Text, nullable=True)
    submission_date = db.Column(db.Date, nullable=True)
    outcome = db.Column(db.String(30), nullable=False, default='draft', index=True)
    document_ref = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    __table_args__ = (
        db.UniqueConstraint('opportunity_id', 'version_number', name='uq_grant_proposal_opportunity_version'),
    )

    __mapper_args__ = {
        'version_id_col': version_id,
    }

    def __repr__(self):
        return f'<GrantProposal opp={self.opportunity_id} v{self.version_number} [{self.outcome}]>'


class GrantOutcomeTemplate(db.Model):
    """Outcome metric definition for a grant, optionally tied to a program case type."""

    __tablename__ = 'grant_outcome_templates'

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    metric_name = db.Column(db.String(120), nullable=False)
    unit = db.Column(db.String(40), nullable=True)
    target_value = db.Column(db.Float, nullable=False)
    baseline_value = db.Column(db.Float, nullable=True)
    program_case_type = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    records = db.relationship('GrantOutcomeRecord', backref='template', cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('grant_id', 'metric_name', name='uq_grant_outcome_template_grant_metric'),
    )

    __mapper_args__ = {
        'version_id_col': version_id,
    }

    def __repr__(self):
        return f'<GrantOutcomeTemplate grant={self.grant_id} metric={self.metric_name}>'


class GrantOutcomeRecord(db.Model):
    """Recorded progress for a grant outcome metric at a point in time."""

    __tablename__ = 'grant_outcome_records'

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey('grant_outcome_templates.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, index=True)
    program_case_id = db.Column(db.Integer, db.ForeignKey('program_cases.id'), nullable=True, index=True)
    current_value = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)
    note = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(40), nullable=False, default='manual')
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    program_case = db.relationship('ProgramCase', backref='grant_outcome_records')

    __mapper_args__ = {
        'version_id_col': version_id,
    }

    def __repr__(self):
        return f'<GrantOutcomeRecord grant={self.grant_id} template={self.template_id} value={self.current_value}>'


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
    'GrantOutcomeTemplate',
    'GrantOutcomeRecord',
    'MembershipTier',
    'MembershipRecord',
    'StewardshipJourney',
    'StewardshipStep',
    'StewardshipEnrollment',
    'Task',
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
    'P2PPage',
    'P2PPageDonation',
    'BeneficiaryAssessment',
    'BeneficiaryReferral',
    'BeneficiaryAppointment',
    'ScheduledReport',
    'VolunteerShift',
    'TrainingCourse',
    'VolunteerTraining',
    'AccountingSyncLog',
]
