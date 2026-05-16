from __future__ import annotations

from pathlib import Path

import pytest

import ngo_homesuite.config as config


def _clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "FLASK_ENV",
        "SECRET_KEY",
        "FLASK_SECRET_KEY",
        "DATABASE_URL",
        "NGO_HOMESUITE_DB_PATH",
        "NGO_HOMESUITE_BACKUP_DIR",
        "LOG_LEVEL",
        "NGO_HOMESUITE_SECRET_FILE",
        "DB_BACKEND",
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_POOL_TIMEOUT_SEC",
        "DB_POOL_RECYCLE_SEC",
        "DB_POOL_PRE_PING",
        "SESSION_STORE_BACKEND",
        "REDIS_URL",
        "REDIS_KEY_PREFIX",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_load_runtime_settings_prefers_env_over_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "env-secret")
    monkeypatch.setenv("NGO_HOMESUITE_DB_PATH", "data/from-env.db")
    monkeypatch.setenv("NGO_HOMESUITE_BACKUP_DIR", "backups/from-env")
    monkeypatch.setenv("LOG_LEVEL", "warning")

    monkeypatch.setattr(
        config,
        "_read_yaml_config",
        lambda: {
            "database": {"path": "data/from-yaml.db"},
            "backup": {"directory": "backups/from-yaml"},
            "logging": {"level": "debug"},
        },
    )

    settings = config.load_runtime_settings()

    assert settings.secret_key == "env-secret"
    assert settings.db_path.endswith(str(Path("data/from-env.db")))
    assert settings.backup_directory.endswith(str(Path("backups/from-env")))
    assert settings.log_level == "WARNING"


def test_load_runtime_settings_uses_yaml_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "yaml-test-secret")
    monkeypatch.setattr(
        config,
        "_read_yaml_config",
        lambda: {
            "database": {"path": "data/from-yaml.db"},
            "backup": {"directory": "backups/from-yaml"},
            "logging": {"level": "error"},
        },
    )

    settings = config.load_runtime_settings()

    assert settings.db_path.endswith(str(Path("data/from-yaml.db")))
    assert settings.backup_directory.endswith(str(Path("backups/from-yaml")))
    assert settings.log_level == "ERROR"


def test_load_runtime_settings_uses_defaults_when_no_env_or_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "default-test-secret")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    settings = config.load_runtime_settings()

    assert settings.database_url.startswith("sqlite:///")
    assert settings.database_backend == "sqlite"
    assert settings.db_path.endswith(str(Path(config.DEFAULT_DB_PATH)))
    assert settings.backup_directory.endswith(str(Path(config.DEFAULT_BACKUP_DIR)))
    assert settings.log_level == "INFO"


def test_load_runtime_settings_production_requires_explicit_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///prod.db")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})
    monkeypatch.setattr(config, "_get_or_create_secret_key", lambda: "generated-secret")

    with pytest.raises(RuntimeError, match="SECRET_KEY or FLASK_SECRET_KEY"):
        config.load_runtime_settings()


def test_load_runtime_settings_production_requires_explicit_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "prod-secret")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        config.load_runtime_settings()


def test_load_runtime_settings_infers_postgres_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "postgres-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/ngo")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    settings = config.load_runtime_settings()
    assert settings.database_backend == "postgresql"


def test_load_runtime_settings_normalizes_legacy_postgres_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "postgres-scheme-secret")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost:5432/ngo")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    settings = config.load_runtime_settings()
    assert settings.database_url.startswith("postgresql://")
    assert settings.database_backend == "postgresql"


def test_load_runtime_settings_accepts_postgresql_psycopg_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "postgres-psycopg-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/ngo")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    settings = config.load_runtime_settings()
    assert settings.database_backend == "postgresql"


def test_load_runtime_settings_accepts_pool_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "pool-secret")
    monkeypatch.setenv("DB_BACKEND", "mysql")
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://user:pass@localhost:3306/ngo")
    monkeypatch.setenv("DB_POOL_SIZE", "25")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "40")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SEC", "55")
    monkeypatch.setenv("DB_POOL_RECYCLE_SEC", "2200")
    monkeypatch.setenv("DB_POOL_PRE_PING", "false")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    settings = config.load_runtime_settings()
    assert settings.database_backend == "mysql"
    assert settings.db_pool_size == 25
    assert settings.db_max_overflow == 40
    assert settings.db_pool_timeout_sec == 55
    assert settings.db_pool_recycle_sec == 2200
    assert settings.db_pool_pre_ping is False


def test_load_runtime_settings_rejects_invalid_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "invalid-url-secret")
    monkeypatch.setenv("DATABASE_URL", "oracle://db-host/ngo")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    with pytest.raises(RuntimeError, match="database_url"):
        config.load_runtime_settings()


def test_load_runtime_settings_accepts_redis_session_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "redis-session-secret")
    monkeypatch.setenv("SESSION_STORE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("REDIS_KEY_PREFIX", "ngohs:test:")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    settings = config.load_runtime_settings()
    assert settings.session_store_backend == "redis"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.redis_key_prefix == "ngohs:test:"


def test_load_runtime_settings_rejects_invalid_session_store_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    monkeypatch.setenv("SECRET_KEY", "invalid-session-backend")
    monkeypatch.setenv("SESSION_STORE_BACKEND", "memcached")
    monkeypatch.setattr(config, "_read_yaml_config", lambda: {})

    with pytest.raises(RuntimeError, match="session_store_backend"):
        config.load_runtime_settings()
