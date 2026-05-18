"""Flask configuration for NGO HomeSuite."""

import os
from datetime import timedelta

from sqlalchemy.pool import StaticPool

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
        'pool_pre_ping': _RUNTIME_SETTINGS.db_pool_pre_ping,
        'pool_recycle': _RUNTIME_SETTINGS.db_pool_recycle_sec,
    }
    if _RUNTIME_SETTINGS.database_backend != 'sqlite' and ':memory:' not in SQLALCHEMY_DATABASE_URI:
        SQLALCHEMY_ENGINE_OPTIONS.update(
            {
                'pool_size': _RUNTIME_SETTINGS.db_pool_size,
                'max_overflow': _RUNTIME_SETTINGS.db_max_overflow,
                'pool_timeout': _RUNTIME_SETTINGS.db_pool_timeout_sec,
            }
        )
    
    # Session
    PERMANENT_SESSION_LIFETIME = timedelta(seconds=_RUNTIME_SETTINGS.permanent_session_lifetime_seconds)
    SESSION_COOKIE_SECURE = _RUNTIME_SETTINGS.session_cookie_secure
    SESSION_COOKIE_HTTPONLY = _RUNTIME_SETTINGS.session_cookie_httponly
    SESSION_COOKIE_SAMESITE = _RUNTIME_SETTINGS.session_cookie_samesite
    SESSION_STORE_BACKEND = _RUNTIME_SETTINGS.session_store_backend
    REDIS_URL = _RUNTIME_SETTINGS.redis_url
    REDIS_KEY_PREFIX = _RUNTIME_SETTINGS.redis_key_prefix
    
    # Login
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = _RUNTIME_SETTINGS.session_cookie_secure
    REMEMBER_COOKIE_SAMESITE = _RUNTIME_SETTINGS.session_cookie_samesite
    
    # Flask-WTF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None
    
    # Flask-Babel
    BABEL_DEFAULT_LOCALE = 'en'
    BABEL_DEFAULT_TIMEZONE = 'UTC'
    
    # Flask-Limiter
    RATELIMIT_ENABLED = _RUNTIME_SETTINGS.rate_limit_enabled
    RATELIMIT_DEFAULT = _RUNTIME_SETTINGS.ratelimit_default
    
    # Logging
    LOG_LEVEL = _RUNTIME_SETTINGS.log_level
    LOG_FILE = _RUNTIME_SETTINGS.log_file
    STRUCTURED_LOGS_JSON = _RUNTIME_SETTINGS.structured_logs_json
    METRICS_ENABLED = _RUNTIME_SETTINGS.metrics_enabled
    
    # Mail (for notifications)
    MAIL_SERVER = _RUNTIME_SETTINGS.mail_server
    MAIL_PORT = _RUNTIME_SETTINGS.mail_port
    MAIL_USE_TLS = _RUNTIME_SETTINGS.mail_use_tls
    MAIL_USERNAME = _RUNTIME_SETTINGS.mail_username
    MAIL_PASSWORD = _RUNTIME_SETTINGS.mail_password
    DEFAULT_MAIL_SENDER = _RUNTIME_SETTINGS.default_mail_sender
    
    # Pagination
    ITEMS_PER_PAGE = 20
    
    # JSON config
    JSON_SORT_KEYS = False

    # Ollama AI (local, in-repo — no external server required)
    APEX_AI_ENABLED = _RUNTIME_SETTINGS.apex_ai_enabled
    OLLAMA_HOST = _RUNTIME_SETTINGS.ollama_host
    OLLAMA_MODEL = _RUNTIME_SETTINGS.ollama_model
    OLLAMA_EMBED_MODEL = _RUNTIME_SETTINGS.ollama_embed_model
    OLLAMA_TIMEOUT_S = _RUNTIME_SETTINGS.ollama_timeout_s
    APEX_TENANT_ID = _RUNTIME_SETTINGS.apex_tenant_id

    # HomeSuite Copilot (local-first)
    COPILOT_ENABLED = _RUNTIME_SETTINGS.copilot_enabled
    COPILOT_INDEX_DIR = _RUNTIME_SETTINGS.copilot_index_dir
    COPILOT_RAG_K = _RUNTIME_SETTINGS.copilot_rag_k
    COPILOT_ALLOW_WEB_TOOLS = _RUNTIME_SETTINGS.copilot_allow_web_tools
    COPILOT_TOOL_ALLOWLIST = ",".join(_RUNTIME_SETTINGS.copilot_tool_allowlist)
    COPILOT_REQUIRE_APPROVAL_TOKEN = _RUNTIME_SETTINGS.copilot_require_approval_token
    COPILOT_APPROVAL_TOKEN_TTL_SEC = _RUNTIME_SETTINGS.copilot_approval_token_ttl_sec
    COPILOT_TOOL_TIMEOUT_SEC = _RUNTIME_SETTINGS.copilot_tool_timeout_sec
    COPILOT_CONVERSATION_MAX_MESSAGES = _RUNTIME_SETTINGS.copilot_conversation_max_messages
    COPILOT_RATE_LIMIT_PER_MIN = _RUNTIME_SETTINGS.copilot_rate_limit_per_min
    CORS_ALLOWED_ORIGINS = _RUNTIME_SETTINGS.cors_allowed_origins
    ENABLE_DEMO_SEED = _RUNTIME_SETTINGS.enable_demo_seed
    DEMO_ADMIN_PASSWORD = _RUNTIME_SETTINGS.demo_admin_password

    # OAuth / SSO providers
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', '')
    GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', '')
    MICROSOFT_CLIENT_ID = os.environ.get('MICROSOFT_CLIENT_ID', '')
    MICROSOFT_CLIENT_SECRET = os.environ.get('MICROSOFT_CLIENT_SECRET', '')
    OKTA_CLIENT_ID = os.environ.get('OKTA_CLIENT_ID', '')
    OKTA_CLIENT_SECRET = os.environ.get('OKTA_CLIENT_SECRET', '')
    OKTA_SERVER_METADATA_URL = os.environ.get('OKTA_SERVER_METADATA_URL', '')
    OAUTH_REDIRECT_BASE = os.environ.get('OAUTH_REDIRECT_BASE', '')  # e.g. https://app.example.com
    HIDE_SSO_OPTIONS = os.environ.get('HIDE_SSO_OPTIONS', '1').strip().lower() in {'1', 'true', 'yes', 'on'}
    SHOW_DEV_LOGIN_CREDENTIALS = os.environ.get('SHOW_DEV_LOGIN_CREDENTIALS', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
    PASSWORD_RESET_TOKEN_TTL_SECONDS = int(os.environ.get('PASSWORD_RESET_TOKEN_TTL_SECONDS', '3600'))

    # Auth support and compliance links
    SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL', '')
    STATUS_PAGE_URL = os.environ.get('STATUS_PAGE_URL', '')
    PRIVACY_URL = os.environ.get('PRIVACY_URL', '')
    TERMS_URL = os.environ.get('TERMS_URL', '')
    COOKIES_URL = os.environ.get('COOKIES_URL', '')

    # WebAuthn / Passkeys
    WEBAUTHN_RP_ID = os.environ.get('WEBAUTHN_RP_ID', '')
    WEBAUTHN_RP_NAME = os.environ.get('WEBAUTHN_RP_NAME', 'NGO HomeSuite')
    WEBAUTHN_ORIGIN = os.environ.get('WEBAUTHN_ORIGIN', '')

    # 2FA enforcement policy
    # Roles in this list are required to enroll in TOTP before accessing the app.
    ROLES_REQUIRING_2FA: list[str] = ['admin']
    # Step-up auth session window in seconds (15 minutes).
    STEP_UP_AUTH_TTL_SECONDS: int = 900


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
    SQLALCHEMY_ENGINE_OPTIONS = {
        'connect_args': {'check_same_thread': False},
        'poolclass': StaticPool,
    }
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    ENABLE_DEMO_SEED = True


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
    return selected
