from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

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
    log_level: str = Field(default="INFO")
    structured_logs_json: bool = Field(default=True)
    metrics_enabled: bool = Field(default=True)


@dataclass(frozen=True)
class AppConfig:
    db_path: str
    backup_directory: str


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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

    return RuntimeSettings(
        flask_env=os.environ.get("FLASK_ENV", "development"),
        secret_key=_get_or_create_secret_key(),
        database_url=database_url,
        db_path=str(Path(str(db_path)).expanduser().resolve(strict=False))
        if db_path not in {":memory:"}
        else db_path,
        backup_directory=str(Path(str(backup_directory)).expanduser().resolve(strict=False)),
        log_level=str(log_level).upper(),
        structured_logs_json=_parse_bool(os.environ.get("STRUCTURED_LOGS_JSON"), True),
        metrics_enabled=_parse_bool(os.environ.get("METRICS_ENABLED"), True),
    )


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
