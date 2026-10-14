from unittest.mock import patch

import pytest

from ngo_homesuite import config
from ngo_homesuite.auth import session
from ngo_homesuite.db import connection
from ngo_homesuite.models import donation, donor, project
from tests.conftest import TEST_PASSWORD


@pytest.fixture
def cli(system, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(system.paths["alpha"]))
    monkeypatch.setattr(config, "TENANT", "alpha")
    monkeypatch.setattr(connection, "DB_PATH", str(system.paths["alpha"]))
    monkeypatch.setattr(session, "CURRENT_USER", {
        "id": system.admin.user_id, "username": "admin", "role": "viewer",
    })


def answers(monkeypatch, values):
    inputs = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))


def test_cli_donor_donation_and_project_use_shared_services(system, cli, monkeypatch):
    answers(monkeypatch, ["CLI donor", "", "", ""])
    with patch("ngo_homesuite.models.donor.cli_services", return_value=system.services):
        donor.add_donor()
    donor_id = system.services.list_entities(system.admin, "donors")[0]["id"]
    answers(monkeypatch, [str(donor_id), "50", "CLI receipt", "", "", "shared-receipt"])
    with patch("ngo_homesuite.models.donation.cli_services", return_value=system.services):
        with patch.object(system.services, "record_donation", wraps=system.services.record_donation) as shared:
            donation.record_donation()
            assert shared.call_count == 1
    assert system.services.list_entities(system.admin, "donations")[0]["amount"] == 50
    answers(monkeypatch, ["CLI project", "", "100"])
    with patch("ngo_homesuite.models.project.cli_services", return_value=system.services):
        project.add_project()
    assert system.services.list_entities(system.admin, "projects")[0]["budget"] == 100
    assert session.CURRENT_USER["role"] == "admin"


def test_cli_permission_checks_database_not_cached_role(system, cli, monkeypatch, capsys):
    with system.store.transaction("alpha", write=True) as conn:
        conn.execute("UPDATE users SET role='viewer' WHERE id=?", (system.admin.user_id,))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Denied user must not reach prompts"))
    donor.add_donor()
    assert "Access denied" in capsys.readouterr().out


def test_cli_user_creation_uses_identity(system, cli):
    from ngo_homesuite.auth.models import create_user, verify_password

    with connection.open_db() as (conn, cur):
        create_user(conn, cur, "cli-user", TEST_PASSWORD, "fundraiser")
    with system.store.transaction("alpha") as conn:
        user = conn.execute("SELECT * FROM users WHERE username='cli-user'").fetchone()
        assert verify_password(TEST_PASSWORD, user["password_salt"], user["password_hash"])
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='user.create' AND entity_id=?",
                            (user["id"],)).fetchone()[0] == 1
