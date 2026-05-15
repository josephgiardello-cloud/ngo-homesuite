"""
Flask configuration for NGO HomeSuite.

Environment-specific settings and base configuration.
"""

import os
from datetime import timedelta

from ngo_homesuite.config import get_runtime_settings


_RUNTIME_SETTINGS = get_runtime_settings()


class Config:
    """Base configuration."""
    
    # Flask
    SECRET_KEY = _RUNTIME_SETTINGS.secret_key
    DEBUG = False
    TESTING = False
    
    # Database
    SQLALCHEMY_DATABASE_URI = _RUNTIME_SETTINGS.database_url
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
    LOG_LEVEL = _RUNTIME_SETTINGS.log_level
    LOG_FILE = os.environ.get('LOG_FILE', 'logs/ngo_homesuite.log')
    STRUCTURED_LOGS_JSON = _RUNTIME_SETTINGS.structured_logs_json
    METRICS_ENABLED = _RUNTIME_SETTINGS.metrics_enabled
    
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

    # Ollama AI (local, in-repo — no external server required)
    APEX_AI_ENABLED = os.environ.get('APEX_AI_ENABLED', 'True') == 'True'  # kept for compat
    OLLAMA_HOST = os.environ.get('OLLAMA_HOST', os.environ.get('APEX_BASE_URL', 'http://localhost:11434'))
    OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', os.environ.get('APEX_MODEL', 'llama3.2'))
    OLLAMA_EMBED_MODEL = os.environ.get('OLLAMA_EMBED_MODEL', 'nomic-embed-text')
    OLLAMA_TIMEOUT_S = float(os.environ.get('OLLAMA_TIMEOUT_S', os.environ.get('APEX_TIMEOUT_S', '120')))
    APEX_TENANT_ID = os.environ.get('APEX_TENANT_ID', 'ngo-default')

    # HomeSuite Copilot (local-first)
    COPILOT_ENABLED = os.environ.get('COPILOT_ENABLED', 'True') == 'True'
    COPILOT_INDEX_DIR = os.environ.get('COPILOT_INDEX_DIR', 'data/copilot_index')
    COPILOT_RAG_K = int(os.environ.get('COPILOT_RAG_K', 6))
    COPILOT_ALLOW_WEB_TOOLS = os.environ.get('COPILOT_ALLOW_WEB_TOOLS', 'False') == 'True'
    COPILOT_TOOL_ALLOWLIST = os.environ.get(
        'COPILOT_TOOL_ALLOWLIST',
        'list_recent_donations,search_donors,donor_profile_insights,summarize_donor,find_similar_donors,rank_donors_for_outreach,suggest_outreach_targets,draft_personalized_appeal,organization_financial_summary,generate_report,generate_grant_report_draft,create_donor,export_donors_snapshot,run_reconciliation,execute_donation_followup_workflow',
    )
    COPILOT_REQUIRE_APPROVAL_TOKEN = os.environ.get('COPILOT_REQUIRE_APPROVAL_TOKEN', 'True') == 'True'
    COPILOT_APPROVAL_TOKEN_TTL_SEC = int(os.environ.get('COPILOT_APPROVAL_TOKEN_TTL_SEC', '300'))


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
    SQLALCHEMY_DATABASE_URI = _RUNTIME_SETTINGS.database_url


# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}


def get_config():
    """Get configuration based on FLASK_ENV."""
    env = _RUNTIME_SETTINGS.flask_env
    selected = config.get(env, DevelopmentConfig)
    if selected is ProductionConfig and not os.environ.get('DATABASE_URL'):
        raise ValueError('DATABASE_URL environment variable must be set in production')
    if selected is ProductionConfig and not (os.environ.get('SECRET_KEY') or os.environ.get('FLASK_SECRET_KEY')):
        raise ValueError('SECRET_KEY or FLASK_SECRET_KEY must be set in production')
    return selected
