"""
Flask application factory and initialization for NGO HomeSuite.

This module creates and configures the Flask application with all extensions.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
import uuid

from flask import Flask, g, request, session, url_for
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_babel import Babel, lazy_gettext as _l
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from ngo_homesuite.flask_config import get_config
from ngo_homesuite.config import get_runtime_settings
from ngo_homesuite.models.core import db, User, Organization, Donor, Donation, Project, Fund, Volunteer, Expense
from ngo_homesuite.errors import init_error_handlers
from ngo_homesuite.app.container import AppContainer
from ngo_homesuite.db.migrate import auto_migrate
from ngo_homesuite.observability import InMemoryMetrics, configure_json_logging, set_request_id
from ngo_homesuite.persistence.models.workflow_tables import WorkflowDefinitionRecord, WorkflowEventRecord, WorkflowInstanceRecord  # noqa: F401


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

    def _latency_bucket(duration_ms: float) -> str:
        if duration_ms < 50.0:
            return "lt_50ms"
        if duration_ms < 200.0:
            return "50_to_199ms"
        if duration_ms < 1000.0:
            return "200_to_999ms"
        return "gte_1000ms"
    # Load configuration
    if config is None:
        config = get_config()
    app.config.from_object(config)

    if app.config.get('SESSION_STORE_BACKEND') == 'redis' and app.config.get('REDIS_URL'):
        try:
            from flask_session import Session  # type: ignore
            import redis  # type: ignore

            app.config['SESSION_TYPE'] = 'redis'
            app.config['SESSION_REDIS'] = redis.from_url(str(app.config.get('REDIS_URL')))
            app.config['SESSION_KEY_PREFIX'] = str(app.config.get('REDIS_KEY_PREFIX', 'ngohs:'))
            app.config['SESSION_PERMANENT'] = False
            Session(app)
            app.logger.info('Redis-backed server session storage enabled')
        except Exception as exc:
            app.logger.warning('Could not enable Redis session storage; falling back to secure cookies: %s', exc)
    
    # Initialize extensions
    db.init_app(app)
    migrate = Migrate(app, db)
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[app.config.get('RATELIMIT_DEFAULT', '200 per day, 50 per hour')],
        enabled=bool(app.config.get('RATELIMIT_ENABLED', True)),
    )
    limiter.init_app(app)
    def select_locale():
        supported = ('en', 'es', 'fr')
        preferred = str(session.get('lang', '')).strip().lower()
        if preferred in supported:
            return preferred
        return request.accept_languages.best_match(supported) or 'en'

    babel = Babel(app, locale_selector=select_locale)
    
    # Login manager
    login_manager = LoginManager(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = _l('Please log in to access this page.')
    login_manager.login_message_category = 'info'
    
    @login_manager.user_loader
    def load_user(user_id):
        """Load user from database by ID."""
        return db.session.get(User, int(user_id))
    
    # Create app context and tables
    with app.app_context():
        db_uri = str(app.config.get('SQLALCHEMY_DATABASE_URI', ''))
        if db_uri.startswith('sqlite:///') and ':memory:' not in db_uri:
            auto_migrate(db_uri.replace('sqlite:///', '', 1))
        db.create_all()
        if bool(app.config.get('ENABLE_DEMO_SEED', False)):
            seed_demo_data(app)
    
    # Register error handlers
    init_error_handlers(app)
    
    # Register blueprints
    from ngo_homesuite.web.main_routes import main_bp
    from ngo_homesuite.web.auth_routes import auth_bp
    from ngo_homesuite.web.ai_routes import ai_bp
    from ngo_homesuite.web.grants_routes import grants_bp
    from ngo_homesuite.web.membership_routes import membership_bp
    from ngo_homesuite.web.tasks_routes import tasks_bp
    from ngo_homesuite.web.program_routes import program_bp
    from ngo_homesuite.web.smart_groups_routes import smart_groups_bp
    from ngo_homesuite.web.p2p_routes import p2p_bp
    from ngo_homesuite.web.integrations_routes import integrations_bp
    from ngo_homesuite.web.reporting_routes import reporting_bp
    from ngo_homesuite.web.admin_routes import admin_bp
    from ngo_homesuite.web.volunteer_routes import volunteer_bp
    from ngo_homesuite.api.v1 import api_v1_bp
    from ngo_homesuite.web.v2_routes import v2_bp
    tony_bp = None
    try:
        from ngo_homesuite.web.tony_routes import tony_bp
    except ModuleNotFoundError as exc:
        app.logger.warning('TONY routes disabled because an optional dependency is missing: %s', exc)

    with app.app_context():
        app.extensions['v2_container'] = AppContainer.build_default()
        app.extensions['metrics'] = app.extensions['v2_container'].metrics if bool(app.config.get('METRICS_ENABLED', True)) else None

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(grants_bp)
    app.register_blueprint(membership_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(program_bp)
    app.register_blueprint(smart_groups_bp)
    app.register_blueprint(p2p_bp)
    app.register_blueprint(integrations_bp)
    app.register_blueprint(reporting_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(volunteer_bp)
    app.register_blueprint(api_v1_bp)
    app.register_blueprint(v2_bp)
    if tony_bp is not None:
        app.register_blueprint(tony_bp)
    
    # Setup logging
    setup_logging(app)
    if bool(app.config.get('STRUCTURED_LOGS_JSON', False)):
        configure_json_logging(app.logger)

    @app.before_request
    def attach_request_trace_context():
        request_id = request.headers.get('X-Request-ID', '').strip() or str(uuid.uuid4())
        g.request_id = request_id
        g.request_start_perf = time.perf_counter()
        set_request_id(request_id)

    @app.teardown_request
    def clear_request_trace_context(_exc):
        set_request_id(None)

    @app.after_request
    def apply_security_headers(response):
        metrics = app.extensions.get('metrics')
        duration_ms = 0.0
        if hasattr(g, 'request_start_perf'):
            duration_ms = (time.perf_counter() - g.request_start_perf) * 1000.0
        labels = {
            'method': request.method,
            'endpoint': request.endpoint or 'unknown',
            'status': str(response.status_code),
        }
        if isinstance(metrics, InMemoryMetrics):
            metrics.inc('http_requests_total', labels=labels)
            metrics.observe('http_request_latency_ms', duration_ms, labels=labels)

        response.headers.setdefault('X-Request-ID', getattr(g, 'request_id', str(uuid.uuid4())))
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('X-Permitted-Cross-Domain-Policies', 'none')
        response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        response.headers.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; img-src 'self' data:; font-src 'self' data:; connect-src 'self'",
        )
        if bool(app.config.get('SESSION_COOKIE_SECURE', False)):
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')

        allowed_origins = app.config.get("CORS_ALLOWED_ORIGINS", []) or []
        req_origin = request.headers.get("Origin", "")
        if req_origin and req_origin in allowed_origins:
            response.headers.setdefault("Access-Control-Allow-Origin", req_origin)
            response.headers.setdefault("Access-Control-Allow-Credentials", "true")
            response.headers.setdefault("Vary", "Origin")
        status_family = f"{response.status_code // 100}xx"
        actor_id = None
        org_id = None
        if current_user.is_authenticated:
            actor_id = getattr(current_user, 'id', None)
            org_id = getattr(current_user, 'organization_id', None)

        app.logger.info(
            'request_completed',
            extra={
                'event_id': 'http.request.completed',
                'extra_fields': {
                    'request_id': getattr(g, 'request_id', None),
                    'org_id': org_id,
                    'actor_id': actor_id,
                    'method': request.method,
                    'path': request.path,
                    'status': status_family,
                    'status_code': response.status_code,
                    'duration_ms': round(duration_ms, 3),
                    'latency_bucket': _latency_bucket(duration_ms),
                },
            },
        )
        return response

    @app.context_processor
    def inject_optional_nav_links():
        tony_full_url = None
        if 'tony.tony_home' in app.view_functions:
            tony_full_url = url_for('tony.tony_home')
        elif 'main.tony_scoring' in app.view_functions:
            tony_full_url = url_for('main.tony_scoring')
        return {'tony_full_url': tony_full_url}
    
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
    admin_user.set_password(get_runtime_settings().demo_admin_password)

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
    settings = get_runtime_settings()
    host = settings.host
    port = settings.port
    debug = settings.flask_debug

    app.run(host=host, port=port, debug=debug)
