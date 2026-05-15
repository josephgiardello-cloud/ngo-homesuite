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
from ngo_homesuite.models.core import db, User
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
    
    app = Flask(__name__)
    
    # Load configuration
    if config is None:
        config = get_config()
    app.config.from_object(config)
    
    # Initialize extensions
    db.init_app(app)
    migrate = Migrate(app, db)
    babel = Babel(app)
    
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
    
    # Register error handlers
    init_error_handlers(app)
    
    # Register blueprints
    from ngo_homesuite.web.main_routes import main_bp
    from ngo_homesuite.web.auth_routes import auth_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    
    # Setup logging
    setup_logging(app)
    
    @babel.localeselector
    def get_locale():
        """Select locale for current request."""
        return 'en'  # Default to English, can be enhanced later
    
    app.logger.info('NGO HomeSuite application initialized')
    
    return app


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
