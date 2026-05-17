"""
Configuration package for NGO HomeSuite.

Exports configuration management and related utilities.
"""

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
]
DB_ENCRYPTION_KEY_ENV = 'NGO_DB_ENCRYPTION_KEY'
