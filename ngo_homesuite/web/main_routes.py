"""
Main routes for NGO HomeSuite.

Home page, dashboard, and main application routes.
"""

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user
from ngo_homesuite.models.core import (
    Organization, Beneficiary, Project, Donation, db
)
from sqlalchemy import func

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    """Home/landing page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('index.html')


@main_bp.route('/dashboard')
@login_required
def dashboard():
    """User dashboard with summary cards."""
    
    # Get stats based on user's organization (if assigned)
    org = current_user.organization
    if org:
        # Get counts for dashboard cards
        beneficiary_count = Beneficiary.query.filter_by(organization_id=org.id, status='active').count()
        project_count = Project.query.filter_by(organization_id=org.id, status='active').count()
        total_donations = db.session.query(func.sum(Donation.amount)).filter_by(organization_id=org.id).scalar() or 0
        
        # Get recent donations
        recent_donations = Donation.query.filter_by(organization_id=org.id).order_by(Donation.donation_date.desc()).limit(5).all()
        
        # Get total project budget
        total_budget = db.session.query(func.sum(Project.budget)).filter_by(organization_id=org.id).scalar() or 0
        
        stats = {
            'organization': org,
            'beneficiary_count': beneficiary_count,
            'project_count': project_count,
            'total_donations': total_donations,
            'total_budget': total_budget,
            'recent_donations': recent_donations,
        }
    else:
        # No organization assigned - show empty state
        stats = {
            'organization': None,
            'beneficiary_count': 0,
            'project_count': 0,
            'total_donations': 0,
            'total_budget': 0,
            'recent_donations': [],
        }
    
    return render_template('dashboard.html', stats=stats)


@main_bp.route('/about')
def about():
    """About page."""
    return render_template('about.html')


@main_bp.route('/help')
def help():
    """Help/documentation page."""
    return render_template('help.html')
