from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = "data/homesuite.db"
DEFAULT_BACKUP_DIR = "backups"

# DB connection pooling (internal)
DB_POOL_SIZE: int = 2


@dataclass(frozen=True)
class AppConfig:
    db_path: str
    backup_directory: str


# Start with safe defaults
_DEFAULT_CONFIG = AppConfig(
    db_path=str(Path(DEFAULT_DB_PATH).resolve()),
    backup_directory=str(Path(DEFAULT_BACKUP_DIR).resolve()),
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


# Public constants
DB_PATH: str = _current_config.db_path
BACKUP_DIRECTORY: str = _current_config.backup_directory

# Back-compat alias (some modules may still import BACKUP_DIR)
BACKUP_DIR = BACKUP_DIRECTORY


# Initialize
apply_config(_DEFAULT_CONFIG)

# Other settings / constants

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
