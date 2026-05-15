from __future__ import annotations

import importlib

import ngo_homesuite.config as runtime_config
import ngo_homesuite.flask_config as flask_config



def test_sqlalchemy_engine_options_for_postgres_backend(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "engine-opt-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/ngo")
    monkeypatch.setenv("DB_BACKEND", "postgresql")
    monkeypatch.setenv("DB_POOL_SIZE", "33")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "44")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SEC", "27")
    monkeypatch.setenv("DB_POOL_RECYCLE_SEC", "900")
    monkeypatch.setenv("DB_POOL_PRE_PING", "true")

    refreshed = runtime_config.load_runtime_settings()
    monkeypatch.setattr(runtime_config, "_RUNTIME_SETTINGS", refreshed, raising=True)
    cfg_module = importlib.reload(flask_config)

    options = cfg_module.Config.SQLALCHEMY_ENGINE_OPTIONS
    assert options["pool_pre_ping"] is True
    assert options["pool_recycle"] == 900
    assert options["pool_size"] == 33
    assert options["max_overflow"] == 44
    assert options["pool_timeout"] == 27



def test_sqlalchemy_engine_options_for_sqlite_memory(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "engine-opt-sqlite-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    refreshed = runtime_config.load_runtime_settings()
    monkeypatch.setattr(runtime_config, "_RUNTIME_SETTINGS", refreshed, raising=True)
    cfg_module = importlib.reload(flask_config)

    options = cfg_module.Config.SQLALCHEMY_ENGINE_OPTIONS
    assert options["pool_pre_ping"] is True
    assert "pool_size" not in options
    assert "max_overflow" not in options
    assert "pool_timeout" not in options
