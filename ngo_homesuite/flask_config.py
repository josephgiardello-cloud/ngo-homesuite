"""Flask configuration for NGO HomeSuite."""

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
    PERMANENT_SESSION_LIFETIME = timedelta(seconds=_RUNTIME_SETTINGS.permanent_session_lifetime_seconds)
    SESSION_COOKIE_SECURE = _RUNTIME_SETTINGS.session_cookie_secure
    SESSION_COOKIE_HTTPONLY = _RUNTIME_SETTINGS.session_cookie_httponly
    SESSION_COOKIE_SAMESITE = _RUNTIME_SETTINGS.session_cookie_samesite
    
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
    CORS_ALLOWED_ORIGINS = _RUNTIME_SETTINGS.cors_allowed_origins


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
    return selected
