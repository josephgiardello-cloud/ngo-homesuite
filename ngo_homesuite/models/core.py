"""
Flask-SQLAlchemy models for NGO HomeSuite.

Core entities:
- User: System users with roles (admin, fundraiser, volunteer_manager, viewer)
- Organization: NGO organization/tenant
- Beneficiary: Individual benefiting from the organization
- Project: Initiative/project within the organization
- Donation: Financial contribution to the organization
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.dialects.sqlite import JSON

db = SQLAlchemy()


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
    role = db.Column(db.String(32), default='viewer', nullable=False)  # admin, fundraiser, volunteer_manager, viewer
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    
    # Organization association
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=True)
    organization = db.relationship('Organization', backref='users')
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    
    def set_password(self, password):
        """Hash and set password."""
        self.password_hash = generate_password_hash(password, method='argon2')
    
    def check_password(self, password):
        """Verify password against hash."""
        return check_password_hash(self.password_hash, password)
    
    def has_role(self, *roles):
        """Check if user has any of the specified roles."""
        return self.role in roles
    
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
    metadata = db.Column(JSON, nullable=True)  # For additional custom fields
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    beneficiaries = db.relationship('Beneficiary', backref='organization', cascade='all, delete-orphan')
    projects = db.relationship('Project', backref='organization', cascade='all, delete-orphan')
    donations = db.relationship('Donation', backref='organization', cascade='all, delete-orphan')
    
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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
    
    # Donor info
    donor_name = db.Column(db.String(200), nullable=False)
    donor_email = db.Column(db.String(120), nullable=True)
    donor_phone = db.Column(db.String(20), nullable=True)
    
    # Donation details
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD', nullable=False)
    donation_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Payment info
    payment_method = db.Column(db.String(50), nullable=True)  # e.g., 'credit_card', 'bank_transfer', 'cash'
    reference_number = db.Column(db.String(100), nullable=True, unique=True)
    
    # Status
    status = db.Column(db.String(50), default='received', nullable=False)  # received, processed, receipted
    
    # Purpose
    purpose = db.Column(db.String(200), nullable=True)  # e.g., 'General Fund', 'Emergency Relief', 'Specific Project'
    notes = db.Column(db.Text, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Donation {self.amount} {self.currency}>'


# Export all models
__all__ = ['db', 'User', 'Organization', 'Beneficiary', 'Project', 'Donation']
