"""Configuration package facade with legacy runtime-config compatibility."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from .config_manager import (
	AppConfig,
	CopilotConfig,
	ConfigValidationError,
	DatabaseConfig,
	Environment,
	FeatureFlagConfig,
	ObservabilityConfig,
	SecretConfig,
	SecurityConfig,
	get_config as _manager_get_config,
)


def _load_legacy_runtime_module():
	legacy_path = Path(__file__).resolve().parents[1] / "config.py"
	spec = importlib.util.spec_from_file_location("ngo_homesuite._legacy_runtime_config", legacy_path)
	if spec is None or spec.loader is None:
		raise RuntimeError(f"Unable to load legacy runtime config module from {legacy_path}")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


_legacy_runtime = _load_legacy_runtime_module()

# Legacy runtime-config API symbols expected across the codebase/tests.
RuntimeSettings = _legacy_runtime.RuntimeSettings
DEFAULT_DB_PATH = _legacy_runtime.DEFAULT_DB_PATH
DEFAULT_BACKUP_DIR = _legacy_runtime.DEFAULT_BACKUP_DIR
DEFAULT_DATABASE_URL = _legacy_runtime.DEFAULT_DATABASE_URL
ALLOW_SQLITE_IN_PRODUCTION_ENV = _legacy_runtime.ALLOW_SQLITE_IN_PRODUCTION_ENV

# Patchable helpers (tests monkeypatch these names on this module).
_read_yaml_config = _legacy_runtime._read_yaml_config
_get_or_create_secret_key = _legacy_runtime._get_or_create_secret_key


def load_runtime_settings() -> RuntimeSettings:
	_legacy_runtime._read_yaml_config = _read_yaml_config
	_legacy_runtime._get_or_create_secret_key = _get_or_create_secret_key
	settings = _legacy_runtime.load_runtime_settings()
	global _RUNTIME_SETTINGS
	_RUNTIME_SETTINGS = settings
	return settings


_RUNTIME_SETTINGS = load_runtime_settings()


def get_runtime_settings() -> RuntimeSettings:
	return _RUNTIME_SETTINGS


def get_config() -> AppConfig:
	return _manager_get_config()


DB_ENCRYPTION_KEY_ENV = _legacy_runtime.DB_ENCRYPTION_KEY_ENV
DB_PATH = str(Path(os.environ.get("DB_PATH", _RUNTIME_SETTINGS.db_path)).resolve())
BACKUP_DIRECTORY = str(Path(os.environ.get("BACKUP_DIRECTORY", _RUNTIME_SETTINGS.backup_directory)).resolve())
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", str(_legacy_runtime.DB_POOL_SIZE)))
DB_SQLCIPHER_KDF_ITERATIONS = int(
	os.environ.get("DB_SQLCIPHER_KDF_ITERATIONS", str(_legacy_runtime.DB_SQLCIPHER_KDF_ITERATIONS))
)
DB_SQLCIPHER_MIN_KDF_ITERATIONS = int(
	os.environ.get("DB_SQLCIPHER_MIN_KDF_ITERATIONS", str(_legacy_runtime.DB_SQLCIPHER_MIN_KDF_ITERATIONS))
)
DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH = int(
	os.environ.get("DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH", str(_legacy_runtime.DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH))
)
DB_SQLCIPHER_REQUIRE_HEX_KEY = os.environ.get(
	"DB_SQLCIPHER_REQUIRE_HEX_KEY", str(_legacy_runtime.DB_SQLCIPHER_REQUIRE_HEX_KEY)
).lower() in {"true", "1", "yes"}
LOGIN_BACKOFF_BASE_SECONDS = float(os.environ.get("LOGIN_BACKOFF_BASE_SECONDS", str(_legacy_runtime.LOGIN_BACKOFF_BASE_SECONDS)))
MAX_LOGIN_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", str(_legacy_runtime.MAX_LOGIN_ATTEMPTS)))
MAX_EMAIL_LENGTH = int(os.environ.get("MAX_EMAIL_LENGTH", str(_legacy_runtime.MAX_EMAIL_LENGTH)))
PHONE_MIN_DIGITS = int(os.environ.get("PHONE_MIN_DIGITS", str(_legacy_runtime.PHONE_MIN_DIGITS)))
PHONE_MAX_DIGITS = int(os.environ.get("PHONE_MAX_DIGITS", str(_legacy_runtime.PHONE_MAX_DIGITS)))
BACKUP_REMINDER_DAYS = int(os.environ.get("BACKUP_REMINDER_DAYS", str(_legacy_runtime.BACKUP_REMINDER_DAYS)))
DEFAULT_EXPORT_DIR = os.environ.get("DEFAULT_EXPORT_DIR", _legacy_runtime.DEFAULT_EXPORT_DIR)

__all__ = [
	"AppConfig",
	"Environment",
	"ConfigValidationError",
	"DatabaseConfig",
	"SecretConfig",
	"SecurityConfig",
	"CopilotConfig",
	"ObservabilityConfig",
	"FeatureFlagConfig",
	"RuntimeSettings",
	"load_runtime_settings",
	"get_runtime_settings",
	"get_config",
	"DEFAULT_DB_PATH",
	"DEFAULT_BACKUP_DIR",
	"DEFAULT_DATABASE_URL",
	"ALLOW_SQLITE_IN_PRODUCTION_ENV",
	"DB_ENCRYPTION_KEY_ENV",
	"DB_PATH",
	"BACKUP_DIRECTORY",
	"DB_POOL_SIZE",
	"DB_SQLCIPHER_KDF_ITERATIONS",
	"DB_SQLCIPHER_MIN_KDF_ITERATIONS",
	"DB_SQLCIPHER_MIN_PASSPHRASE_LENGTH",
	"DB_SQLCIPHER_REQUIRE_HEX_KEY",
	"LOGIN_BACKOFF_BASE_SECONDS",
	"MAX_LOGIN_ATTEMPTS",
	"MAX_EMAIL_LENGTH",
	"PHONE_MIN_DIGITS",
	"PHONE_MAX_DIGITS",
	"BACKUP_REMINDER_DAYS",
	"DEFAULT_EXPORT_DIR",
]
