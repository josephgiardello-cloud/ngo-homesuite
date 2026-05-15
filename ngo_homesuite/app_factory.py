"""
Flask application factory and initialization for NGO HomeSuite.

This module creates and configures the Flask application with all extensions.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import os

from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_babel import Babel, lazy_gettext as _l

from ngo_homesuite.flask_config import get_config
from ngo_homesuite.models.core import db, User, Organization, Donor, Donation, Project, Fund, Volunteer, Expense
from ngo_homesuite.errors import init_error_handlers


def create_app(config=None):
    """
    Application factory.
    
    Creates and configures a Flask application with all extensions.
    
    Args:
        config: Configuration object (if None, uses environment-based config)
    
    Returns:
        Configured Flask application instance
    """
    
    app = Flask(__name__, template_folder='web/templates')
    
    # Load configuration
    if config is None:
        config = get_config()
    app.config.from_object(config)
    
    # Initialize extensions
    db.init_app(app)
    migrate = Migrate(app, db)
    def select_locale():
        return 'en'

    babel = Babel(app, locale_selector=select_locale)
    
    # Login manager
    login_manager = LoginManager(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = _l('Please log in to access this page.')
    login_manager.login_message_category = 'info'
    
    @login_manager.user_loader
    def load_user(user_id):
        """Load user from database by ID."""
        return User.query.get(int(user_id))
    
    # Create app context and tables
    with app.app_context():
        db.create_all()
        seed_demo_data(app)
    
    # Register error handlers
    init_error_handlers(app)
    
    # Register blueprints
    from ngo_homesuite.web.main_routes import main_bp
    from ngo_homesuite.web.auth_routes import auth_bp
    from ngo_homesuite.web.ai_routes import ai_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(ai_bp)
    
    # Setup logging
    setup_logging(app)
    
    app.logger.info('NGO HomeSuite application initialized')
    
    return app


def seed_demo_data(app):
    """Seed minimal dummy data so first-run dashboard is immediately usable."""

    if User.query.count() > 0:
        return

    org = Organization(
        name='Community Hope Initiative',
        slug='community-hope-initiative',
        description='Demo organization for NGO HomeSuite.',
        mission='Serve families through transparent community programs.',
        country='US',
        city='Austin',
        is_active=True,
    )
    db.session.add(org)
    db.session.flush()

    admin_user = User(
        username='admin',
        email='admin@ngohomesuite.local',
        first_name='System',
        last_name='Admin',
        role='admin',
        organization_id=org.id,
    )
    admin_user.set_password(os.environ.get('NGO_DEMO_ADMIN_PASSWORD', 'admin123!'))

    staff_user = User(
        username='staff',
        email='staff@ngohomesuite.local',
        first_name='Program',
        last_name='Staff',
        role='staff',
        organization_id=org.id,
    )
    staff_user.set_password('staff123!')

    volunteer_user = User(
        username='volunteer',
        email='volunteer@ngohomesuite.local',
        first_name='Community',
        last_name='Volunteer',
        role='volunteer',
        organization_id=org.id,
    )
    volunteer_user.set_password('volunteer123!')

    viewer_user = User(
        username='viewer',
        email='viewer@ngohomesuite.local',
        first_name='ReadOnly',
        last_name='Viewer',
        role='viewer',
        organization_id=org.id,
    )
    viewer_user.set_password('viewer123!')

    db.session.add_all([admin_user, staff_user, volunteer_user, viewer_user])

    donors = [
        Donor(organization_id=org.id, name='Ana Martins', email='ana@example.org', phone='+1-555-0101', donor_type='individual'),
        Donor(organization_id=org.id, name='Bright Future Foundation', email='contact@brightfuture.org', donor_type='foundation'),
    ]
    db.session.add_all(donors)
    db.session.flush()

    fund = Fund(
        organization_id=org.id,
        name='General Fund',
        description='General operating fund for mission-critical activities.',
        is_active=True,
    )
    db.session.add(fund)
    db.session.flush()

    project = Project(
        organization_id=org.id,
        name='Youth Learning Program',
        description='After-school tutoring and mentoring for youth.',
        program='Education',
        budget=25000,
        spent=5400,
        status='active',
    )
    db.session.add(project)
    db.session.flush()

    db.session.add(
        Donation(
            organization_id=org.id,
            donor_id=donors[0].id,
            donor_name=donors[0].name,
            donor_email=donors[0].email,
            donor_phone=donors[0].phone,
            amount=1200,
            currency='USD',
            payment_method='bank_transfer',
            status='received',
            purpose='Education materials',
            reference_number='DEMO-001',
            project_id=project.id,
            fund_id=fund.id,
        )
    )

    db.session.add(
        Volunteer(
            organization_id=org.id,
            name='Luis Parker',
            email='luis.volunteer@example.org',
            phone='+1-555-0109',
            hours_logged=14.5,
            status='active',
        )
    )

    db.session.add(
        Expense(
            organization_id=org.id,
            project_id=project.id,
            fund_id=fund.id,
            amount=780,
            currency='USD',
            payee='Learning Supplies Co',
            description='Starter packs for 30 students',
        )
    )

    db.session.commit()
    app.logger.info('Demo seed data created: users, donors, donations, projects, funds, volunteers, expenses')


def setup_logging(app):
    """
    Configure logging for the application.
    
    Sets up both file and console logging based on configuration.
    """
    
    if app.debug:
        return  # Skip logging setup in debug mode
    
    # Create logs directory if it doesn't exist
    log_path = Path(app.config.get('LOG_FILE', 'logs/ngo_homesuite.log'))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Configure file handler
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10485760,  # 10MB
        backupCount=10
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    
    log_level = getattr(logging, app.config.get('LOG_LEVEL', 'INFO'))
    file_handler.setLevel(log_level)
    
    app.logger.addHandler(file_handler)
    app.logger.setLevel(log_level)
    app.logger.info('NGO HomeSuite startup')


# Application entry point
if __name__ == '__main__':
    app = create_app()
    
    # Get host and port from environment
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    app.run(host=host, port=port, debug=debug)
