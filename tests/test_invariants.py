from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import sqlite3

from hypothesis import HealthCheck, given, settings, strategies as st
import pytest

from ngo_homesuite.domain.services import Allocation, Actor, minor_units
from ngo_homesuite.domain.store import DomainError, TenantStore

PROPERTY = settings(max_examples=35, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)


@given(st.integers(min_value=1, max_value=99999999999999))
def test_money_roundtrip(cents):
    assert minor_units(Decimal(cents) / 100) == cents


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0", "0.001", "1000000000000", "not money"])
def test_invalid_money_rejected(amount):
    with pytest.raises(DomainError):
        minor_units(amount)


@PROPERTY
@given(st.integers(min_value=1, max_value=100000), st.integers(min_value=1, max_value=8))
def test_donation_idempotency_and_audit(system, cents, retries):
    s = system.services
    donor = s.create_donor(system.fundraiser, "Donor")
    key = f"receipt-{donor}"
    ids = [s.record_donation(system.fundraiser, donor, Decimal(cents) / 100, key) for _ in range(retries)]
    assert len(set(ids)) == 1
    with system.store.transaction("alpha") as conn:
        assert conn.execute("SELECT COUNT(*) FROM donations WHERE id=?", (ids[0],)).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='donation.create' AND entity_id=?", (ids[0],),
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == conn.execute(
            "SELECT COUNT(*) FROM outbox",
        ).fetchone()[0]
        payload = json.loads(conn.execute(
            "SELECT details_json FROM audit_log WHERE action='donation.create' AND entity_id=?", (ids[0],),
        ).fetchone()[0])
        assert payload["details"]["amount_minor"] == cents
    with pytest.raises(DomainError, match="another request"):
        s.record_donation(system.fundraiser, donor, Decimal(cents + 1) / 100, key)


@PROPERTY
@given(st.integers(min_value=1, max_value=10000))
def test_allocation_exact_limit_and_atomic_replacement(system, cents):
    s = system.services
    donor = s.create_donor(system.admin, "Allocation donor")
    donation = s.record_donation(system.admin, donor, Decimal(cents) / 100, f"allocate-{donor}")
    s.allocate_donation(system.admin, donation, [Allocation("general", None, Decimal(cents) / 100)])
    with pytest.raises(DomainError, match="exceed"):
        s.allocate_donation(system.admin, donation, [Allocation("general", None, Decimal(cents + 1) / 100)])
    with system.store.transaction("alpha") as conn:
        amount = conn.execute(
            "SELECT amount FROM donation_allocations WHERE donation_id=?", (donation,),
        ).fetchone()[0]
        assert minor_units(amount) == cents


def test_service_permissions_ignore_forged_role_and_revocation(system):
    with pytest.raises(DomainError, match="Access denied"):
        system.services.create_project(system.fundraiser, "No", "10")
    with pytest.raises(DomainError, match="Access denied"):
        system.services.create_donor(system.viewer, "No")
    with system.store.transaction("alpha", write=True) as conn:
        conn.execute("UPDATE users SET role='viewer' WHERE id=?", (system.admin.user_id,))
    with pytest.raises(DomainError, match="Access denied"):
        system.services.create_donor(system.admin, "Revoked")


def test_tenant_isolation_and_foreign_references(system):
    donor = system.services.create_donor(system.admin, "Alpha only")
    assert system.services.list_entities(system.outsider, "donors") == []
    with pytest.raises(DomainError, match="not found"):
        system.services.record_donation(system.outsider, donor, "5", "foreign")
    with pytest.raises(DomainError, match="Unknown organization"):
        system.services.list_entities(Actor("../alpha", system.admin.user_id), "donors")
    with pytest.raises(ValueError, match="share"):
        TenantStore({"one": system.paths["alpha"], "two": system.paths["alpha"]})


def test_concurrent_idempotency(system):
    donor = system.services.create_donor(system.admin, "Concurrent")
    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(
            lambda _: system.services.record_donation(system.admin, donor, "50", "same-receipt"), range(16),
        ))
    assert len(set(ids)) == 1


def test_concurrent_budget_reservation_and_two_admin_payment(system):
    s = system.services
    project = s.create_project(system.admin, "Budget", "100")
    grants = [s.create_grant(system.fundraiser, project, "Grant", "60") for _ in range(2)]

    def approve(grant):
        try:
            s.transition_grant(system.admin, grant, "approve")
            return grant
        except DomainError as exc:
            assert exc.status == 409
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(approve, grants))
    approved = [result for result in results if result is not None]
    assert len(approved) == 1
    with pytest.raises(DomainError, match="other than"):
        s.transition_grant(system.admin, approved[0], "pay")
    with pytest.raises(DomainError):
        s.set_project_spent(system.admin, project, "41")
    s.transition_grant(system.payer, approved[0], "pay")
    with pytest.raises(DomainError):
        s.set_project_spent(system.admin, project, "59.99")
    for action in ("pay", "approve", "cancel"):
        with pytest.raises(DomainError, match="Cannot"):
            s.transition_grant(system.payer, approved[0], action)
    assert s.list_entities(system.admin, "projects")[0]["spent"] == 60


@pytest.mark.parametrize("table", ["audit_log", "outbox"])
def test_failed_audit_or_event_rolls_back_business_state(system, table):
    donor = system.services.create_donor(system.admin, "Failure donor")
    with system.store.transaction("alpha", write=True) as conn:
        conn.execute(f"""CREATE TRIGGER fail_event BEFORE INSERT ON {table}
                      BEGIN SELECT RAISE(ABORT, 'injected write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        system.services.record_donation(system.admin, donor, "25", "rollback")
    with system.store.transaction("alpha") as conn:
        assert conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='donation.create'").fetchone()[0] == 0
