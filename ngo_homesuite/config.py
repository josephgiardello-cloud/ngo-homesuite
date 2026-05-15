from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

DEFAULT_DB_PATH = "data/homesuite.db"
DEFAULT_BACKUP_DIR = "backups"
DEFAULT_DATABASE_URL = "sqlite:///ngo_homesuite.db"
DEFAULT_CONFIG_CANDIDATES = (
    "ngo-homesuite.yaml",
    "ngo_homesuite.yaml",
)

# DB connection pooling (internal)
DB_POOL_SIZE: int = 2


class RuntimeSettings(BaseModel):
    flask_env: str = Field(default="development")
    secret_key: str
    database_url: str = Field(default=DEFAULT_DATABASE_URL)
    db_path: str = Field(default_factory=lambda: str(Path(DEFAULT_DB_PATH).resolve()))
    backup_directory: str = Field(default_factory=lambda: str(Path(DEFAULT_BACKUP_DIR).resolve()))
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=5000)
    flask_debug: bool = Field(default=False)
    flask_testing: bool = Field(default=False)
    log_level: str = Field(default="INFO")
    structured_logs_json: bool = Field(default=True)
    metrics_enabled: bool = Field(default=True)
    log_file: str = Field(default="logs/ngo_homesuite.log")

    rate_limit_enabled: bool = Field(default=True)
    ratelimit_default: str = Field(default="200 per day, 50 per hour")

    permanent_session_lifetime_seconds: int = Field(default=2_592_000)
    session_cookie_secure: bool = Field(default=False)
    session_cookie_httponly: bool = Field(default=True)
    session_cookie_samesite: str = Field(default="Lax")

    mail_server: str = Field(default="localhost")
    mail_port: int = Field(default=25)
    mail_use_tls: bool = Field(default=False)
    mail_username: str | None = None
    mail_password: str | None = None
    default_mail_sender: str = Field(default="noreply@ngohomesuite.local")

    apex_ai_enabled: bool = Field(default=True)
    ollama_host: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="llama3.2")
    ollama_embed_model: str = Field(default="nomic-embed-text")
    ollama_timeout_s: float = Field(default=120.0)
    apex_tenant_id: str = Field(default="ngo-default")

    copilot_enabled: bool = Field(default=True)
    copilot_index_dir: str = Field(default="data/copilot_index")
    copilot_rag_k: int = Field(default=6)
    copilot_allow_web_tools: bool = Field(default=False)
    copilot_tool_allowlist: list[str] = Field(default_factory=list)
    copilot_require_approval_token: bool = Field(default=True)
    copilot_approval_token_ttl_sec: int = Field(default=300)
    copilot_tool_timeout_sec: float = Field(default=8.0)
    copilot_conversation_max_messages: int = Field(default=200)

    cors_allowed_origins: list[str] = Field(default_factory=list)

    migration_timeout_sec: float = Field(default=30.0)
    backup_before_migrate: bool = Field(default=True)
    restore_backup_on_migration_fail: bool = Field(default=True)
    require_backup_before_migrate: bool = Field(default=True)
    migration_backup_warn_only: bool = Field(default=False)

    demo_admin_password: str = Field(default="admin123!")

    @field_validator("flask_env")
    @classmethod
    def _validate_flask_env(cls, value: str) -> str:
        allowed = {"development", "testing", "production"}
        normalized = str(value).strip().lower()
        if normalized not in allowed:
            raise ValueError(f"flask_env must be one of {sorted(allowed)}, got: {value!r}")
        return normalized

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        normalized = str(value).strip().upper()
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got: {value!r}")
        return normalized

    @field_validator("session_cookie_samesite")
    @classmethod
    def _validate_samesite(cls, value: str) -> str:
        allowed = {"Lax", "Strict", "None"}
        normalized = str(value).strip().capitalize()
        if normalized not in allowed:
            raise ValueError(f"session_cookie_samesite must be one of {sorted(allowed)}, got: {value!r}")
        return normalized

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator("mail_port")
    @classmethod
    def _validate_mail_port(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError("mail_port must be between 1 and 65535")
        return value

    @field_validator("ollama_timeout_s", "copilot_tool_timeout_sec", "migration_timeout_sec")
    @classmethod
    def _validate_positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout values must be > 0")
        return value

    @field_validator("copilot_rag_k", "copilot_approval_token_ttl_sec", "copilot_conversation_max_messages")
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be > 0")
        return value


@dataclass(frozen=True)
class AppConfig:
    db_path: str
    backup_directory: str


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _read_yaml_config() -> dict[str, Any]:
    explicit = os.environ.get("NGO_HOMESUITE_CONFIG")
    candidate_paths: list[Path] = []
    if explicit:
        candidate_paths.append(Path(explicit))
    candidate_paths.extend(Path(name) for name in DEFAULT_CONFIG_CANDIDATES)

    for path in candidate_paths:
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            return data
    return {}


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _get_or_create_secret_key() -> str:
    env_secret = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if env_secret:
        return env_secret

    secret_file = Path(os.environ.get("NGO_HOMESUITE_SECRET_FILE", "data/.secret_key")).resolve()
    if secret_file.exists():
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    secret_file.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    secret_file.write_text(generated + "\n", encoding="utf-8")
    try:
        if os.name != "nt":
            secret_file.chmod(0o600)
    except OSError:
        pass
    return generated


def load_runtime_settings() -> RuntimeSettings:
    yaml_cfg = _read_yaml_config()

    db_path = (
        os.environ.get("NGO_HOMESUITE_DB_PATH")
        or _nested(yaml_cfg, "database", "path")
        or str(Path(DEFAULT_DB_PATH).resolve())
    )

    backup_directory = (
        os.environ.get("NGO_HOMESUITE_BACKUP_DIR")
        or _nested(yaml_cfg, "backup", "directory")
        or str(Path(DEFAULT_BACKUP_DIR).resolve())
    )

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        if db_path == ":memory:" or str(db_path).startswith("sqlite:///"):
            database_url = str(db_path)
        else:
            database_url = f"sqlite:///{db_path}"

    log_level = (
        os.environ.get("LOG_LEVEL")
        or _nested(yaml_cfg, "logging", "level")
        or "INFO"
    )

    values: dict[str, Any] = {
        "flask_env": os.environ.get("FLASK_ENV", "development"),
        "secret_key": _get_or_create_secret_key(),
        "database_url": database_url,
        "db_path": str(Path(str(db_path)).expanduser().resolve(strict=False)) if db_path not in {":memory:"} else db_path,
        "backup_directory": str(Path(str(backup_directory)).expanduser().resolve(strict=False)),
        "host": os.environ.get("HOST", "127.0.0.1"),
        "port": int(os.environ.get("PORT", "5000")),
        "flask_debug": _parse_bool(os.environ.get("FLASK_DEBUG"), False),
        "flask_testing": _parse_bool(os.environ.get("FLASK_TESTING"), False),
        "log_level": str(log_level).upper(),
        "structured_logs_json": _parse_bool(os.environ.get("STRUCTURED_LOGS_JSON"), True),
        "metrics_enabled": _parse_bool(os.environ.get("METRICS_ENABLED"), True),
        "log_file": os.environ.get("LOG_FILE", "logs/ngo_homesuite.log"),
        "rate_limit_enabled": _parse_bool(os.environ.get("RATE_LIMIT_ENABLED"), True),
        "ratelimit_default": os.environ.get("RATELIMIT_DEFAULT", "200 per day, 50 per hour"),
        "permanent_session_lifetime_seconds": int(os.environ.get("PERMANENT_SESSION_LIFETIME", "2592000")),
        "session_cookie_secure": _parse_bool(os.environ.get("SESSION_COOKIE_SECURE"), False),
        "session_cookie_httponly": _parse_bool(os.environ.get("SESSION_COOKIE_HTTPONLY"), True),
        "session_cookie_samesite": os.environ.get("SESSION_COOKIE_SAMESITE", "Lax"),
        "mail_server": os.environ.get("MAIL_SERVER", "localhost"),
        "mail_port": int(os.environ.get("MAIL_PORT", "25")),
        "mail_use_tls": _parse_bool(os.environ.get("MAIL_USE_TLS"), False),
        "mail_username": os.environ.get("MAIL_USERNAME"),
        "mail_password": os.environ.get("MAIL_PASSWORD"),
        "default_mail_sender": os.environ.get("DEFAULT_MAIL_SENDER", "noreply@ngohomesuite.local"),
        "apex_ai_enabled": _parse_bool(os.environ.get("APEX_AI_ENABLED"), True),
        "ollama_host": os.environ.get("OLLAMA_HOST") or os.environ.get("APEX_BASE_URL") or "http://localhost:11434",
        "ollama_model": os.environ.get("OLLAMA_MODEL") or os.environ.get("APEX_MODEL") or "llama3.2",
        "ollama_embed_model": os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        "ollama_timeout_s": float(os.environ.get("OLLAMA_TIMEOUT_S") or os.environ.get("APEX_TIMEOUT_S") or "120"),
        "apex_tenant_id": os.environ.get("APEX_TENANT_ID", "ngo-default"),
        "copilot_enabled": _parse_bool(os.environ.get("COPILOT_ENABLED"), True),
        "copilot_index_dir": os.environ.get("COPILOT_INDEX_DIR", "data/copilot_index"),
        "copilot_rag_k": int(os.environ.get("COPILOT_RAG_K", "6")),
        "copilot_allow_web_tools": _parse_bool(os.environ.get("COPILOT_ALLOW_WEB_TOOLS"), False),
        "copilot_tool_allowlist": _split_csv(os.environ.get("COPILOT_TOOL_ALLOWLIST")),
        "copilot_require_approval_token": _parse_bool(os.environ.get("COPILOT_REQUIRE_APPROVAL_TOKEN"), True),
        "copilot_approval_token_ttl_sec": int(os.environ.get("COPILOT_APPROVAL_TOKEN_TTL_SEC", "300")),
        "copilot_tool_timeout_sec": float(os.environ.get("COPILOT_TOOL_TIMEOUT_SEC", "8")),
        "copilot_conversation_max_messages": int(os.environ.get("COPILOT_CONVERSATION_MAX_MESSAGES", "200")),
        "cors_allowed_origins": _split_csv(os.environ.get("CORS_ALLOWED_ORIGINS")),
        "migration_timeout_sec": float(os.environ.get("NGO_HOMESUITE_MIGRATION_TIMEOUT_SEC", "30")),
        "backup_before_migrate": _parse_bool(os.environ.get("NGO_HOMESUITE_BACKUP_BEFORE_MIGRATE"), True),
        "restore_backup_on_migration_fail": _parse_bool(os.environ.get("NGO_HOMESUITE_RESTORE_BACKUP_ON_MIGRATION_FAIL"), True),
        "require_backup_before_migrate": _parse_bool(os.environ.get("NGO_HOMESUITE_REQUIRE_BACKUP_BEFORE_MIGRATE"), True),
        "migration_backup_warn_only": _parse_bool(os.environ.get("NGO_HOMESUITE_MIGRATION_BACKUP_WARN_ONLY"), False),
        "demo_admin_password": os.environ.get("NGO_DEMO_ADMIN_PASSWORD", "admin123!"),
    }

    try:
        settings = RuntimeSettings(**values)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid runtime configuration: {exc}") from exc

    if settings.flask_env == "production":
        if not (os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")):
            raise RuntimeError(
                "Invalid runtime configuration: production requires SECRET_KEY or FLASK_SECRET_KEY to be explicitly set"
            )
        if not os.environ.get("DATABASE_URL"):
            raise RuntimeError(
                "Invalid runtime configuration: production requires DATABASE_URL to be explicitly set"
            )

    return settings


_RUNTIME_SETTINGS = load_runtime_settings()

# Backward-compatible app config shape used by legacy DB code.
_DEFAULT_CONFIG = AppConfig(
    db_path=_RUNTIME_SETTINGS.db_path,
    backup_directory=_RUNTIME_SETTINGS.backup_directory,
)
_current_config: AppConfig = _DEFAULT_CONFIG


def _update_constants() -> None:
    global DB_PATH, BACKUP_DIRECTORY, BACKUP_DIR
    DB_PATH = _current_config.db_path
    BACKUP_DIRECTORY = _current_config.backup_directory
    BACKUP_DIR = BACKUP_DIRECTORY


def apply_config(new_config: AppConfig) -> None:
    global _current_config
    _current_config = new_config
    _update_constants()


def get_config() -> AppConfig:
    return _current_config


def get_runtime_settings() -> RuntimeSettings:
    return _RUNTIME_SETTINGS


# Public constants
DB_PATH: str = _current_config.db_path
BACKUP_DIRECTORY: str = _current_config.backup_directory

# Back-compat alias (some modules may still import BACKUP_DIR)
BACKUP_DIR = BACKUP_DIRECTORY


# Initialize
apply_config(_DEFAULT_CONFIG)

# Encryption
DB_ENCRYPTION_KEY_ENV: str = "NGO_HOMESUITE_DB_KEY"

# SQLCipher hardening policies (applies only when DB_ENCRYPTION_KEY_ENV is set)
DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH: int = 12
DB_SQLCIPHER_KDF_ITERATIONS: int = 256_000
DB_SQLCIPHER_MIN_KDF_ITERATIONS: int = 256_000
DB_SQLCIPHER_REQUIRE_HEX_KEY: bool = False

DEFAULT_EXPORT_DIR = "exports"
BACKUP_REMINDER_DAYS = 7

PBKDF2_ITERATIONS = 260_000
MAX_LOGIN_ATTEMPTS = 5
LOGIN_BACKOFF_BASE_SECONDS = 0.5

MAX_EMAIL_LENGTH = 254
PHONE_MIN_DIGITS = 7
PHONE_MAX_DIGITS = 15
