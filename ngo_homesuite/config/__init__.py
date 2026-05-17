"""
Configuration package for NGO HomeSuite.

Exports configuration management and related utilities.
"""

import os
from pathlib import Path

from .config_manager import (
	AppConfig,
	Environment,
	ConfigValidationError,
	DatabaseConfig,
	SecretConfig,
	SecurityConfig,
	CopilotConfig,
	ObservabilityConfig,
	FeatureFlagConfig,
	get_config,
)

# Alias for backward compatibility / internal imports
get_runtime_settings = get_config


DB_ENCRYPTION_KEY_ENV = 'NGO_DB_ENCRYPTION_KEY'
DB_PATH = str(Path(os.environ.get('DB_PATH', 'ngo_homesuite.sqlite3')).resolve())
BACKUP_DIRECTORY = str(Path(os.environ.get('BACKUP_DIRECTORY', 'backups')).resolve())
DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '2'))
DB_SQLCIPHER_KDF_ITERATIONS = int(os.environ.get('DB_SQLCIPHER_KDF_ITERATIONS', '64000'))
DB_SQLCIPHER_MIN_KDF_ITERATIONS = int(os.environ.get('DB_SQLCIPHER_MIN_KDF_ITERATIONS', '4000'))
DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH = int(os.environ.get('DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH', '32'))
DB_SQLCIPHER_REQUIRE_HEX_KEY = os.environ.get('DB_SQLCIPHER_REQUIRE_HEX_KEY', 'true').lower() in {'true', '1', 'yes'}
LOGIN_BACKOFF_BASE_SECONDS = int(os.environ.get('LOGIN_BACKOFF_BASE_SECONDS', '2'))
MAX_LOGIN_ATTEMPTS = int(os.environ.get('MAX_LOGIN_ATTEMPTS', '5'))
MAX_EMAIL_LENGTH = int(os.environ.get('MAX_EMAIL_LENGTH', '254'))
PHONE_MIN_DIGITS = int(os.environ.get('PHONE_MIN_DIGITS', '7'))
PHONE_MAX_DIGITS = int(os.environ.get('PHONE_MAX_DIGITS', '15'))
BACKUP_REMINDER_DAYS = int(os.environ.get('BACKUP_REMINDER_DAYS', '7'))
DEFAULT_EXPORT_DIR = os.environ.get('DEFAULT_EXPORT_DIR', 'exports')

__all__ = [
	'AppConfig',
	'Environment',
	'ConfigValidationError',
	'DatabaseConfig',
	'SecretConfig',
	'SecurityConfig',
	'CopilotConfig',
	'ObservabilityConfig',
	'FeatureFlagConfig',
	'get_config',
	'get_runtime_settings',
	'DB_ENCRYPTION_KEY_ENV',
	'DB_PATH',
	'BACKUP_DIRECTORY',
	'DB_POOL_SIZE',
	'DB_SQLCIPHER_KDF_ITERATIONS',
	'DB_SQLCIPHER_MIN_KDF_ITERATIONS',
	'DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH',
	'DB_SQLCIPHER_REQUIRE_HEX_KEY',
	'LOGIN_BACKOFF_BASE_SECONDS',
	'MAX_LOGIN_ATTEMPTS',
	'MAX_EMAIL_LENGTH',
	'PHONE_MIN_DIGITS',
	'PHONE_MAX_DIGITS',
	'BACKUP_REMINDER_DAYS',
	'DEFAULT_EXPORT_DIR',
]
