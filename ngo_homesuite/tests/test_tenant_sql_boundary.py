from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest
from flask import Blueprint, Flask, g
from flask_login import LoginManager, login_user
from sqlalchemy import create_engine, delete, event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import aliased

from ngo_homesuite.audit.security_events import SecurityAuditEvent
from ngo_homesuite.db.rls import background_tenant_context, register_rls_listeners
from ngo_homesuite.models.core import (
    AIConversation,
    AIMessage,
    Donation,
    DonationReceipt,
    Donor,
    Organization,
    P2PPage,
    P2PPageDonation,
    StewardshipJourney,
    StewardshipStep,
    User,
    Volunteer,
    db,
)


@pytest.fixture(scope="module")
def boundary_app(tmp_path_factory):
    app = Flask(__name__)
    url = os.environ.get("TENANT_TEST_DATABASE_URL", "sqlite:///:memory:")
    schema = f"tenant_proof_{uuid4().hex}"
    admin_engine = None
    options = {}
    encrypted_path = None
    if os.environ.get("TENANT_TEST_SQLCIPHER") == "1":
        from sqlcipher3 import dbapi2

        encrypted_path = tmp_path_factory.mktemp("tenant-cipher") / "boundary.sqlite3"
        url = "sqlite:///" + encrypted_path.as_posix()
        options = {"module": dbapi2}
    if url.startswith("postgresql"):
        admin_engine = create_engine(url)
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        options = {"connect_args": {"options": f"-csearch_path={schema}"}}
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_ENGINE_OPTIONS=options,
        TESTING=True,
    )
    db.init_app(app)
    LoginManager(app)
    auth = Blueprint("auth", __name__)

    @auth.route("/auth/boundary-proof")
    def auth_boundary_proof():
        return "", 204

    app.register_blueprint(auth)
    with app.app_context():
        if encrypted_path is not None:
            key = secrets.token_hex(32)

            @event.listens_for(db.engine, "connect")
            def set_key(connection, record):
                connection.execute(f'''PRAGMA key = "x'{key}'"''')
                assert connection.execute("PRAGMA cipher_version").fetchone()[0]
        db.create_all()
    register_rls_listeners(app)
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    if encrypted_path is not None:
        assert encrypted_path.read_bytes()[:16] != b"SQLite format 3\x00"
        with sqlite3.connect(encrypted_path) as plaintext, pytest.raises(sqlite3.DatabaseError):
            plaintext.execute("SELECT * FROM organizations").fetchall()
    if admin_engine is not None:
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture()
def boundary_rows(boundary_app):
    with boundary_app.app_context():
        suffix = uuid4().hex
        orgs = [Organization(name=f"Boundary {x}", slug=f"boundary-{x}-{suffix}") for x in ("a", "b")]
        db.session.add_all(orgs)
        db.session.flush()
        parents = []
        children = []
        for org in orgs:
            conversation = AIConversation(organization_id=org.id, session_id=uuid4().hex)
            donation = Donation(organization_id=org.id, donor_name="Private", amount=10, currency="USD")
            journey = StewardshipJourney(organization_id=org.id, name="Private", trigger="new_donor")
            donor = Donor(organization_id=org.id, name="Private")
            db.session.add_all([conversation, donation, journey, donor])
            db.session.flush()
            page = P2PPage(organization_id=org.id, donor_id=donor.id, title="Private", public_slug=uuid4().hex)
            db.session.add(page)
            db.session.flush()
            rows = [
                AIMessage(conversation_id=conversation.id, role="user", content="private"),
                DonationReceipt(donation_id=donation.id, receipt_number=uuid4().hex),
                StewardshipStep(journey_id=journey.id, step_type="wait"),
                P2PPageDonation(page_id=page.id, donation_id=donation.id),
            ]
            db.session.add_all(rows)
            parents.append((conversation, donation, journey, page))
            children.append(rows)
        db.session.commit()
        org_ids = [org.id for org in orgs]
        parent_ids = [[row.id for row in group] for group in parents]
        child_ids = [
            [row.id if hasattr(row, "id") else (row.page_id, row.donation_id) for row in group]
            for group in children
        ]
        db.session.expunge_all()
        yield org_ids, parent_ids, child_ids
        db.session.rollback()


@pytest.mark.parametrize("mutation", ["role", "organization", "other_user", "non_auth_route"])
def test_tenantless_self_authentication_exception_cannot_authorize_other_writes(
    boundary_app, boundary_rows, mutation,
):
    orgs, _, _ = boundary_rows
    suffix = uuid4().hex
    users = [
        User(username=f"tenantless-{index}-{suffix}", email=f"{index}-{suffix}@example.test", role="viewer", is_active=True)
        for index in range(2)
    ]
    for user in users:
        user.set_password(secrets.token_urlsafe(32))
    db.session.add_all(users)
    db.session.commit()
    path = "/outside-auth" if mutation == "non_auth_route" else "/auth/boundary-proof"
    with boundary_app.test_request_context(path):
        login_user(users[0])
        g.tenant_membership_required = True
        if mutation != "non_auth_route":
            users[0].last_login = datetime.now(timezone.utc)
            db.session.flush()
        if mutation == "role":
            users[0].role = "admin"
        elif mutation == "organization":
            users[0].organization_id = orgs[1]
        elif mutation == "other_user":
            users[1].last_login = datetime.now(timezone.utc)
        else:
            users[0].last_login = datetime.now(timezone.utc)
        with pytest.raises(PermissionError, match="no tenant organization"):
            db.session.flush()
        db.session.rollback()
        assert users[0].role == "viewer"
        assert users[0].organization_id is None
        assert users[1].last_login is None


@pytest.mark.parametrize("index,model", [
    (0, AIMessage), (1, DonationReceipt), (2, StewardshipStep), (3, P2PPageDonation),
])
def test_indirect_read_alias_count_id_and_missing_membership(boundary_rows, index, model):
    orgs, _, ids = boundary_rows
    for own, other in ((0, 1), (1, 0)):
        g.organization_id = orgs[own]
        alias = aliased(model)
        rows = list(db.session.scalars(select(alias)))
        assert len(rows) == 1
        assert db.session.scalar(select(func.count()).select_from(model)) == 1
        assert db.session.get(model, ids[other][index]) is None
        db.session.expunge_all()
    g.organization_id = None
    g.tenant_membership_required = True
    assert list(db.session.scalars(select(model))) == []


def test_postgres_migration_deploy_runner_fresh_install_and_rollback(monkeypatch):
    from ngo_homesuite import app_factory
    from ngo_homesuite.db import deploy_migrate
    from ngo_homesuite.flask_config import TestingConfig

    url = os.environ.get("TENANT_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        if os.environ.get("REQUIRE_POSTGRES_TENANT_PROOF") == "1":
            pytest.fail("Required PostgreSQL engine proof has no PostgreSQL URL")
        pytest.skip("PostgreSQL-specific production migration runner proof")
    schema = f"deploy_proof_{uuid4().hex}"
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    applications = []

    class ProofConfig(TestingConfig):
        FLASK_ENV = "production"
        SQLALCHEMY_DATABASE_URI = url
        SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict[str, dict[str, str]]] = {"connect_args": {"options": f"-csearch_path={schema}"}}
        ENABLE_DEMO_SEED = False

    def create_proof_app():
        application = app_factory.create_app(ProofConfig)
        applications.append(application)
        assert application.config["APP_PROCESS_ROLE"] == "migrator"
        return application

    monkeypatch.setattr(deploy_migrate, "create_app", create_proof_app)
    monkeypatch.setenv("APP_PROCESS_ROLE", "web")
    try:
        assert deploy_migrate.run_deploy_migration("upgrade")["revision"] == "head"
        assert os.environ["APP_PROCESS_ROLE"] == "web"
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261014_0002"
            assert connection.scalar(
                text("SELECT count(*) FROM pg_policies WHERE schemaname = :schema"),
                {"schema": schema},
            ) == 71
        assert deploy_migrate.run_deploy_migration("downgrade", revision="20260604_0001")["action"] == "downgrade"
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260604_0001"
            assert connection.scalar(
                text("SELECT count(*) FROM pg_policies WHERE schemaname = :schema"),
                {"schema": schema},
            ) == 0
        assert os.environ["APP_PROCESS_ROLE"] == "web"
        with pytest.raises(RuntimeError, match="NOSUPERUSER NOBYPASSRLS"):
            app_factory.create_app(ProofConfig)
    finally:
        for application in applications:
            with application.app_context():
                db.session.remove()
                db.engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.parametrize("index,model,key", [
    (0, AIMessage, "conversation_id"),
    (1, DonationReceipt, "donation_id"),
    (2, StewardshipStep, "journey_id"),
    (3, P2PPageDonation, "page_id"),
])
def test_indirect_reparent_and_delete_are_denied(boundary_rows, index, model, key):
    orgs, parents, ids = boundary_rows
    g.organization_id = orgs[0]
    row = db.session.get(model, ids[0][index])
    setattr(row, key, parents[1][index])
    with pytest.raises(PermissionError, match="indirect parent"):
        db.session.flush()
    db.session.rollback()
    g.organization_id = orgs[1]
    row = db.session.get(model, ids[1][index])
    g.organization_id = orgs[0]
    db.session.delete(row)
    with pytest.raises(PermissionError, match="indirect parent"):
        db.session.flush()
    db.session.rollback()


def test_association_requires_both_parents_same_tenant(boundary_rows):
    orgs, parents, _ = boundary_rows
    g.organization_id = orgs[0]
    db.session.add(P2PPageDonation(page_id=parents[0][3], donation_id=parents[1][1]))
    with pytest.raises(PermissionError, match="indirect parent"):
        db.session.flush()
    db.session.rollback()


def test_secondary_relationship_and_relationship_reparent_are_denied(boundary_rows):
    orgs, parents, ids = boundary_rows
    g.organization_id = orgs[1]
    foreign_donation = db.session.get(Donation, parents[1][1])
    foreign_conversation = db.session.get(AIConversation, parents[1][0])
    g.organization_id = orgs[0]
    page = db.session.get(P2PPage, parents[0][3])
    page.donations.append(foreign_donation)
    with pytest.raises(PermissionError, match="Tenant isolation violation"):
        db.session.flush()
    db.session.rollback()
    message = db.session.get(AIMessage, ids[0][0])
    message.conversation = foreign_conversation
    with pytest.raises(PermissionError, match="Tenant isolation violation"):
        db.session.flush()
    db.session.rollback()


def test_new_indirect_parent_and_child_flush_together(boundary_rows):
    orgs, _, _ = boundary_rows
    g.organization_id = orgs[0]
    conversation = AIConversation(organization_id=orgs[0], session_id=uuid4().hex)
    conversation.messages.append(AIMessage(role="user", content="Own child"))
    db.session.add(conversation)
    db.session.commit()
    assert conversation.messages[0].conversation_id == conversation.id


@pytest.mark.parametrize("allowlist", [None, [], [0], [True], ["1"], [1, 1]])
def test_scheduler_allowlist_fails_before_callback(boundary_app, allowlist):
    from ngo_homesuite.db.tenant_jobs import run_tenant_job

    boundary_app.config["TENANT_WORKER_ORGANIZATION_IDS"] = allowlist
    with pytest.raises(PermissionError, match="allowlist"):
        run_tenant_job(
            boundary_app, name="proof", callback=lambda: pytest.fail("Unauthorised job executed"),
        )


def test_scheduler_executes_each_explicit_tenant_and_persists_audits(boundary_app, boundary_rows):
    from ngo_homesuite.db.rls import require_tenant_context
    from ngo_homesuite.db.tenant_jobs import run_tenant_job

    orgs, _, _ = boundary_rows
    boundary_app.config["TENANT_WORKER_ORGANIZATION_IDS"] = orgs
    seen = []

    def dispatch():
        organization_id = require_tenant_context()
        donations = list(db.session.scalars(select(Donation)))
        assert {row.organization_id for row in donations} == {organization_id}
        seen.append(organization_id)

    run_tenant_job(boundary_app, name="proof", callback=dispatch)
    assert seen == orgs
    events = list(db.session.scalars(select(SecurityAuditEvent).where(SecurityAuditEvent.resource_org_id.in_(orgs))))
    assert len(events) == 4
    assert [event.payload["event"] for event in events].count("tenant_context_started") == 2


@pytest.mark.parametrize("statement", [
    text("SELECT * FROM donations"),
    select(Donation.__table__),
    update(Donation).values(amount=999),
    delete(Donation),
])
def test_strict_boundary_rejects_raw_core_and_bulk(boundary_app, boundary_rows, statement):
    orgs, _, _ = boundary_rows
    events = []
    with background_tenant_context(orgs[0], actor="proof-worker", reason="negative SQL proof", audit=events.append):
        with pytest.raises(PermissionError, match="Raw SQL and bulk DML"):
            db.session.execute(statement)
        with db.engine.connect() as conn:
            with pytest.raises(PermissionError, match="Unscoped engine SQL"):
                conn.execute(select(Donation.__table__))
            with pytest.raises(PermissionError, match="Unscoped engine SQL"):
                conn.exec_driver_sql("SELECT * FROM donations")
    assert events[-1]["outcome"] == "completed"


def test_strict_boundary_allows_validated_flush_and_rejects_bypass(boundary_rows):
    orgs, _, _ = boundary_rows
    with background_tenant_context(orgs[0], actor="proof-worker", reason="flush proof", audit=lambda event: None):
        row = Volunteer(organization_id=orgs[0], name="Own worker record")
        db.session.add(row)
        db.session.commit()
        assert db.session.get(Volunteer, row.id).organization_id == orgs[0]
        with pytest.raises(PermissionError, match="bypass"):
            db.session.execute(select(Volunteer).execution_options(skip_tenant_rls=True))
        with pytest.raises(PermissionError, match="Unscoped engine SQL"):
            db.session.bulk_insert_mappings(Volunteer, [{"organization_id": orgs[0], "name": "Bypass"}])
        db.session.rollback()


def test_strict_request_context_rejects_absent_invalid_tenant(boundary_app, boundary_rows):
    boundary_app.config["TENANT_SQL_GUARD_ENABLED"] = True
    try:
        with boundary_app.test_request_context():
            for invalid in (None, False, 0, -1, "1", "bad"):
                g.organization_id = invalid
                with pytest.raises(PermissionError, match="tenant context"):
                    db.session.execute(select(Donation))
    finally:
        boundary_app.config["TENANT_SQL_GUARD_ENABLED"] = False


def test_worker_rejects_unknown_tenant_before_start_audit(boundary_rows):
    events = []
    with pytest.raises(PermissionError, match="Unknown tenant"), background_tenant_context(
        2147483647, actor="worker", reason="negative proof", audit=events.append,
    ):
        pytest.fail("Unknown tenant must never execute")
    assert events == []


def test_postgres_migration_failure_recovery_and_native_isolation(boundary_app, boundary_rows, monkeypatch):
    if db.engine.dialect.name != "postgresql":
        if os.environ.get("REQUIRE_POSTGRES_TENANT_PROOF") == "1":
            pytest.fail("PostgreSQL tenant proof requires a PostgreSQL test engine")
        pytest.skip("PostgreSQL-specific migration/role proof")
    from alembic import command
    from alembic.config import Config
    from alembic.operations import Operations

    root = Path(__file__).resolve().parents[2]
    config = Config()
    config.set_main_option("script_location", str(root / "migrations"))
    orgs, parents, ids = boundary_rows
    g.organization_id = None
    db.session.remove()
    command.upgrade(config, "20260604_0001")
    original = Operations.execute
    calls = 0

    def fail_mid_migration(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("injected policy migration failure")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Operations, "execute", fail_mid_migration)
        with pytest.raises(RuntimeError, match="injected policy"):
            command.upgrade(config, "head")
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260604_0001"
        assert conn.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname = current_schema()")).scalar_one() == 0
    command.upgrade(config, "head")
    role = f"tenant_proof_role_{uuid4().hex}"
    with db.engine.begin() as conn:
        schema = conn.execute(text("SELECT current_schema()")).scalar_one()
        conn.execute(text(f'CREATE ROLE "{role}" NOSUPERUSER NOBYPASSRLS'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
        conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"'))
        conn.execute(text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"'))
        assert conn.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname = current_schema()")).scalar_one() == 71
    try:
        for own, other in ((0, 1), (1, 0)):
            g.organization_id = orgs[own]
            with db.engine.begin() as conn:
                conn.execute(text(f'SET LOCAL ROLE "{role}"'))
                flags = conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
                assert flags == (False, False)
                assert conn.execute(text("SELECT id FROM donations")).scalars().all() == [parents[own][1]]
                assert conn.execute(text("SELECT id FROM ai_messages")).scalars().all() == [ids[own][0]]
                assert conn.execute(text("UPDATE donations SET amount = 999 WHERE id = :id"), {"id": parents[other][1]}).rowcount == 0
                assert conn.execute(text("DELETE FROM donation_receipts WHERE id = :id"), {"id": ids[other][1]}).rowcount == 0
                with pytest.raises(DBAPIError, match="row-level security"), conn.begin_nested():
                    conn.execute(
                        text("INSERT INTO ai_messages(conversation_id, role, content, created_at) VALUES(:id, 'user', 'denied', CURRENT_TIMESTAMP)"),
                        {"id": parents[other][0]},
                    )
                with pytest.raises(DBAPIError, match="row-level security"), conn.begin_nested():
                    conn.execute(
                        text("INSERT INTO p2p_page_donations(page_id, donation_id) VALUES(:page, :donation)"),
                        {"page": parents[own][3], "donation": parents[other][1]},
                    )
        g.organization_id = None
        with db.engine.begin() as conn:
            conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            assert conn.execute(text("SELECT id FROM donations")).scalars().all() == []
        command.downgrade(config, "20260604_0001")
        with db.engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname = current_schema()")).scalar_one() == 0
            assert conn.execute(text("SELECT amount FROM donations WHERE id = :id"), {"id": parents[0][1]}).scalar_one() == 10
    finally:
        g.organization_id = None
        with db.engine.begin() as conn:
            conn.execute(text(f'DROP OWNED BY "{role}"'))
            conn.execute(text(f'DROP ROLE "{role}"'))
