"""
Comprehensive tests for Centralized Configuration Manager.

Validates:
âœ… Configuration loading from environment variables
âœ… Type-safe configuration access
âœ… Secret validation and masking
âœ… Environment-specific profiles
âœ… Feature flag support
âœ… Configuration validation at startup
"""

import os
import pytest
from ngo_homesuite.config.config_manager import (
    AppConfig,
    Environment,
    ConfigValidationError,
    DatabaseConfig,
    SecretConfig,
    SecurityConfig,
)


class TestDatabaseConfig:
    """Test database configuration."""

    def test_database_config_valid(self):
        """
        **Scenario**: Valid database config.
        
        **Assertions**: Config validates without errors.
        """
        config = DatabaseConfig(
            url="sqlite:///test.db",
            pool_size=10,
        )
        config.validate()  # Should not raise

    def test_database_config_missing_url(self):
        """
        **Scenario**: Database config without URL.
        
        **Assertions**: Validation fails with clear error.
        """
        config = DatabaseConfig(url="")
        
        with pytest.raises(ConfigValidationError, match="DATABASE_URL required"):
            config.validate()

    def test_database_config_encryption_without_key(self):
        """
        **Scenario**: Encryption enabled but no key provided.
        
        **Assertions**: Validation fails.
        """
        config = DatabaseConfig(
            url="sqlite:///test.db",
            encryption_enabled=True,
            encryption_key=None,
        )
        
        with pytest.raises(ConfigValidationError, match="ENCRYPTION_KEY required"):
            config.validate()

    def test_database_config_invalid_pool_size(self):
        """
        **Scenario**: Pool size out of valid range.
        
        **Assertions**: Validation fails.
        """
        config = DatabaseConfig(
            url="sqlite:///test.db",
            pool_size=200,  # Too large
        )
        
        with pytest.raises(ConfigValidationError, match="1-100"):
            config.validate()


class TestSecretsConfig:
    """Test secrets configuration."""

    def test_secrets_config_valid(self):
        """
        **Scenario**: Valid secrets config.
        
        **Assertions**: Config validates.
        """
        config = SecretConfig(
            secret_key="x" * 32,  # 32 chars minimum
        )
        config.validate()

    def test_secrets_config_missing_secret_key(self):
        """
        **Scenario**: No SECRET_KEY provided.
        
        **Assertions**: Validation fails.
        """
        config = SecretConfig(secret_key="")
        
        with pytest.raises(ConfigValidationError, match="SECRET_KEY required"):
            config.validate()

    def test_secrets_config_short_secret_key(self):
        """
        **Scenario**: SECRET_KEY too short (<32 chars).
        
        **Assertions**: Validation fails with entropy warning.
        """
        config = SecretConfig(secret_key="short")
        
        with pytest.raises(ConfigValidationError, match="256"):
            config.validate()


class TestEnvironmentLoadingWithMocking:
    """Test configuration loading from environment."""

    def test_load_database_config_from_env(self, monkeypatch):
        """
        **Scenario**: Load database config from environment variables.
        
        **Assertions**: All values loaded correctly.
        """
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        monkeypatch.setenv('DATABASE_POOL_SIZE', '20')
        monkeypatch.setenv('DATABASE_POOL_RECYCLE', '1800')
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        
        config = AppConfig(Environment.TESTING)
        
        assert config.database.url == 'sqlite:///test.db'
        assert config.database.pool_size == 20
        assert config.database.pool_recycle == 1800

    def test_load_secrets_from_env(self, monkeypatch):
        """
        **Scenario**: Load secrets from environment.
        
        **Assertions**: All secrets loaded (validation on init).
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DB_ENCRYPTION_KEY', 'encryption_key_32_chars_long___')
        monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_123456')
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        
        config = AppConfig(Environment.TESTING)
        
        assert config.secrets.secret_key == 'x' * 32
        assert config.secrets.db_encryption_key == 'encryption_key_32_chars_long___'
        assert config.secrets.stripe_secret_key == 'sk_test_123456'

    def test_load_boolean_env_variables(self, monkeypatch):
        """
        **Scenario**: Load boolean environment variables.
        
        **Assertions**: Various boolean representations parsed correctly.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        monkeypatch.setenv('MINION_ENABLED', 'true')
        monkeypatch.setenv('DB_ENCRYPTION_ENABLED', '1')
        monkeypatch.setenv('ENFORCE_HTTPS', 'yes')
        
        config = AppConfig(Environment.TESTING)
        
        assert config.minion.enabled is True
        assert config.database.encryption_enabled is True
        assert config.security.enforce_https is True

    def test_load_integer_env_variables(self, monkeypatch):
        """
        **Scenario**: Load integer environment variables.
        
        **Assertions**: Integers parsed correctly.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        monkeypatch.setenv('MINION_TIMEOUT', '45')
        monkeypatch.setenv('RATE_LIMIT_REQUESTS', '200')
        
        config = AppConfig(Environment.TESTING)
        
        assert config.minion.timeout == 45
        assert config.security.rate_limit_requests == 200


class TestSecretMasking:
    """Test secret masking in logs/output."""

    def test_to_dict_masks_secrets(self, monkeypatch):
        """
        **Scenario**: Convert config to dict with secrets masked.
        
        **Assertions**: All secrets replaced with ***MASKED***.
        """
        monkeypatch.setenv('SECRET_KEY', 'my_secret_key_12345678901234')
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_secret')
        
        config = AppConfig(Environment.TESTING)
        config_dict = config.to_dict(mask_secrets=True)
        
        # All secrets should be masked
        assert config_dict['secrets']['secret_key'] == '***MASKED***'
        assert config_dict['secrets']['stripe_secret_key'] == '***MASKED***'

    def test_to_dict_no_mask_for_debug(self, monkeypatch):
        """
        **Scenario**: Get unmasked config for debugging (internal use only).
        
        **Assertions**: Real values exposed (use with caution).
        """
        monkeypatch.setenv('SECRET_KEY', 'my_secret_key_12345678901234')
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        
        config = AppConfig(Environment.TESTING)
        config_dict = config.to_dict(mask_secrets=False)
        
        # Secrets should be visible (debug only)
        assert config_dict['secrets']['secret_key'] == 'my_secret_key_12345678901234'


class TestFeatureFlags:
    """Test feature flag configuration."""

    def test_feature_flag_enabled(self, monkeypatch):
        """
        **Scenario**: Enable feature flag via environment.
        
        **Assertions**: Flag correctly reflects enabled state.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        monkeypatch.setenv('ENABLE_SQLCIPHER', 'true')
        
        config = AppConfig(Environment.TESTING)
        
        assert config.feature_flags.enable_sqlcipher is True
        assert config.feature_flags.is_enabled('enable_sqlcipher') is True

    def test_feature_flag_disabled_by_default(self, monkeypatch):
        """
        **Scenario**: Feature flags disabled by default.
        
        **Assertions**: All flags default to False.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        # Don't set any feature flags
        
        config = AppConfig(Environment.TESTING)
        
        assert config.feature_flags.enable_sqlcipher is False
        assert config.feature_flags.enable_minion_v2 is False
        assert config.feature_flags.enable_new_dashboard is False


class TestEnvironmentProfiles:
    """Test environment-specific configuration."""

    def test_production_environment_strict_security(self, monkeypatch):
        """
        **Scenario**: Production environment enforces security defaults.
        
        **Assertions**: HTTPS enforced, strict CSP.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///prod.db')
        
        config = AppConfig(Environment.PRODUCTION)
        
        assert config.security.enforce_https is True
        assert config.security.csp_strict_mode is True
        assert config.database.echo is False

    def test_development_environment_relaxed(self, monkeypatch):
        """
        **Scenario**: Development environment allows debugging.
        
        **Assertions**: Debug features enabled by default.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///dev.db')
        
        config = AppConfig(Environment.DEVELOPMENT)
        
        # Development can be more permissive
        assert config.observability.log_level in ['INFO', 'DEBUG']


class TestMinionConfig:
    """Test Minion configuration."""

    def test_minion_tool_allowlist_parsing(self, monkeypatch):
        """
        **Scenario**: Parse comma-separated tool allowlist.
        
        **Assertions**: Tools correctly split and trimmed.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        monkeypatch.setenv('MINION_TOOL_ALLOWLIST', 'tool_a, tool_b, tool_c')
        
        config = AppConfig(Environment.TESTING)
        
        assert config.minion.tool_allowlist == ['tool_a', 'tool_b', 'tool_c']

    def test_minion_circuit_breaker_config(self, monkeypatch):
        """
        **Scenario**: Circuit breaker settings configurable.
        
        **Assertions**: Threshold and timeout loaded.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        monkeypatch.setenv('MINION_CIRCUIT_BREAKER_THRESHOLD', '10')
        monkeypatch.setenv('MINION_CIRCUIT_BREAKER_TIMEOUT', '600')
        
        config = AppConfig(Environment.TESTING)
        
        assert config.minion.circuit_breaker_threshold == 10
        assert config.minion.circuit_breaker_timeout == 600


class TestConfigValidationOnInit:
    """Test that validation happens at config initialization."""

    def test_validation_fails_on_init_missing_secret_key(self, monkeypatch):
        """
        **Scenario**: Config initialization fails if SECRET_KEY missing.
        
        **Assertions**: ConfigValidationError raised before app starts.
        """
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        # Don't set SECRET_KEY
        
        with pytest.raises(ConfigValidationError, match="SECRET_KEY required"):
            AppConfig(Environment.TESTING)

    def test_validation_fails_on_init_invalid_database(self, monkeypatch):
        """
        **Scenario**: Config initialization fails if DATABASE_URL missing.
        
        **Assertions**: ConfigValidationError raised.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        # Don't set DATABASE_URL
        
        with pytest.raises(ConfigValidationError, match="DATABASE_URL required"):
            AppConfig(Environment.TESTING)

    def test_all_validations_run_on_init(self, monkeypatch):
        """
        **Scenario**: All config sections validated during init.
        
        **Assertions**: Invalid values caught early.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        monkeypatch.setenv('MINION_TIMEOUT', '2')  # Invalid: too low
        
        with pytest.raises(ConfigValidationError, match="MINION_TIMEOUT"):
            AppConfig(Environment.TESTING)


class TestConfigSerialization:
    """Test config serialization for logging/debugging."""

    def test_to_dict_includes_all_sections(self, monkeypatch):
        """
        **Scenario**: to_dict() includes all config sections.
        
        **Assertions**: Database, secrets, minion, security all present.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        
        config = AppConfig(Environment.TESTING)
        config_dict = config.to_dict(mask_secrets=True)
        
        assert 'database' in config_dict
        assert 'secrets' in config_dict
        assert 'minion' in config_dict
        assert 'security' in config_dict
        assert 'observability' in config_dict
        assert 'feature_flags' in config_dict

    def test_to_dict_environment_included(self, monkeypatch):
        """
        **Scenario**: Environment name included in serialized config.
        
        **Assertions**: Environment correctly identified.
        """
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///test.db')
        
        config = AppConfig(Environment.PRODUCTION)
        config_dict = config.to_dict()
        
        assert config_dict['environment'] == 'production'

