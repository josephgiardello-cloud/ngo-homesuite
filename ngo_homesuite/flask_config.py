"""
Flask configuration for NGO HomeSuite.

Environment-specific settings and base configuration.
"""

import os
from datetime import timedelta


class Config:
    """Base configuration."""
    
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = False
    TESTING = False
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///ngo_homesuite.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 3600,
    }
    
    # Session
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Login
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    REMEMBER_COOKIE_HTTPONLY = True
    
    # Flask-WTF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None
    
    # Flask-Babel
    BABEL_DEFAULT_LOCALE = 'en'
    BABEL_DEFAULT_TIMEZONE = 'UTC'
    
    # Flask-Limiter
    RATELIMIT_ENABLED = os.environ.get('RATE_LIMIT_ENABLED', 'True') == 'True'
    RATELIMIT_DEFAULT = '200 per day, 50 per hour'
    
    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FILE = os.environ.get('LOG_FILE', 'logs/ngo_homesuite.log')
    
    # Mail (for notifications)
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'localhost')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 25))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'False') == 'True'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    DEFAULT_MAIL_SENDER = os.environ.get('DEFAULT_MAIL_SENDER', 'noreply@ngohomesuite.local')
    
    # Pagination
    ITEMS_PER_PAGE = 20
    
    # JSON config
    JSON_SORT_KEYS = False

    # Apex Sovereign AI
    APEX_AI_ENABLED = os.environ.get('APEX_AI_ENABLED', 'True') == 'True'
    APEX_BASE_URL = os.environ.get('APEX_BASE_URL', 'http://localhost:8000')
    APEX_API_TOKEN = os.environ.get('APEX_API_TOKEN', '')
    APEX_MODEL = os.environ.get('APEX_MODEL', 'llama3.2')
    APEX_TENANT_ID = os.environ.get('APEX_TENANT_ID', 'ngo-default')
    APEX_TIMEOUT_S = float(os.environ.get('APEX_TIMEOUT_S', '120'))


class DevelopmentConfig(Config):
    """Development configuration."""
    
    DEBUG = True
    TESTING = False
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = True


class TestingConfig(Config):
    """Testing configuration."""
    
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False


class ProductionConfig(Config):
    """Production configuration."""
    
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True  # Requires HTTPS
    WTF_CSRF_ENABLED = True
    RATELIMIT_ENABLED = True
    
    # Production database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')


# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}


def get_config():
    """Get configuration based on FLASK_ENV."""
    env = os.environ.get('FLASK_ENV', 'development')
    selected = config.get(env, DevelopmentConfig)
    if selected is ProductionConfig and not os.environ.get('DATABASE_URL'):
        raise ValueError('DATABASE_URL environment variable must be set in production')
    return selected
