import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.app.config import load_store
from backend.app.provision import main
from backend.app.server import app_factory
from ngo_homesuite.domain.store import DomainError


def test_server_factory_uses_configured_manifest(system, tmp_path, monkeypatch):
    manifest = tmp_path / "tenants.json"
    manifest.write_text(json.dumps({"alpha": "alpha.db", "beta": "beta.db"}))
    monkeypatch.setenv("NGOHS_TENANTS_FILE", str(manifest))
    monkeypatch.setenv("NGOHS_INSECURE_LOCAL", "1")
    monkeypatch.setenv("NGOHS_ALLOWED_HOSTS", "testserver")
    with TestClient(app_factory()) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/health", headers={"host": "untrusted.example"}).status_code == 400


def test_provision_command_fresh_install_without_touching_default_database(tmp_path, monkeypatch):
    path = tmp_path / "new.db"
    monkeypatch.setattr("sys.argv", ["provision", str(path)])
    main()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone() == ("2",)
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone() == (0,)


@pytest.mark.parametrize("config", [{}, {"../alpha": "alpha.db"}, {"alpha": 12},
                                    {"alpha": "alpha.db", "beta": "alpha.db"}])
def test_invalid_registry_rejected(tmp_path, config):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        load_store(path)


def test_unprovisioned_or_unmigrated_tenant_fails_closed(system):
    with system.store.transaction("alpha", write=True) as conn:
        conn.execute("UPDATE app_meta SET value='1' WHERE key='schema_version'")
    with pytest.raises(DomainError, match="migration required"):
        system.services.list_entities(system.admin, "donors")


def test_bootstrap_and_cross_tenant_user_creation_denied(system):
    from tests.conftest import TEST_PASSWORD

    with pytest.raises(DomainError, match="Bootstrap"):
        system.identity.create_user("alpha", "unexpected", TEST_PASSWORD, "admin")
    with pytest.raises(DomainError, match="mismatch"):
        system.identity.create_user("beta", "unexpected", TEST_PASSWORD, "admin", actor=system.admin)
