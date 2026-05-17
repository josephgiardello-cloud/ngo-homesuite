from __future__ import annotations

from datetime import date

import pytest

from ngo_homesuite.grants.services import lifecycle as grant_service
from ngo_homesuite.models.core import Expense, GrantExpenseAllocation, Organization, db
from ngo_homesuite.services.expense_service import ExpenseService


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield


def _mk_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def _mk_awarded_grant(org_id: int, *, amount_awarded: float = 1000.0):
    grant = grant_service.create_grant(
        organization_id=org_id,
        funder_name="Hardening Funder",
        title="Hardening Grant",
        amount_requested=amount_awarded,
    )
    grant_service.advance_grant_status(grant.id, org_id, new_status="submitted")
    grant_service.advance_grant_status(
        grant.id,
        org_id,
        new_status="awarded",
        amount_awarded=amount_awarded,
    )
    grant_service.add_disbursement(
        grant_id=grant.id,
        organization_id=org_id,
        amount=amount_awarded,
        received_date=date(2026, 5, 16),
    )
    return grant


def test_budget_line_update_and_delete_emit_audit_events(ctx, monkeypatch):
    actions: list[str] = []

    def _capture(action: str, **kwargs):
        actions.append(action)

    monkeypatch.setattr(grant_service, "audit", _capture)

    org = _mk_org("Hardening Org A", "hardening-org-a")
    grant = _mk_awarded_grant(org.id, amount_awarded=300.0)

    line = grant_service.create_budget_line(
        grant.id,
        org.id,
        category="supplies",
        allocated_amount=300.0,
    )
    grant_service.update_budget_line(
        grant.id,
        org.id,
        line.id,
        allocated_amount=280.0,
        expected_version=line.version_id,
    )
    refreshed = db.session.get(type(line), line.id)
    assert refreshed is not None
    grant_service.delete_budget_line(
        grant.id,
        org.id,
        line.id,
        expected_version=refreshed.version_id,
    )

    assert "grant.budget_line.create" in actions
    assert "grant.budget_line.update" in actions
    assert "grant.budget_line.delete" in actions


def test_allocation_create_emits_expected_audit_action(ctx, monkeypatch):
    actions: list[str] = []

    def _capture(action: str, **kwargs):
        actions.append(action)

    monkeypatch.setattr(grant_service, "audit", _capture)

    org = _mk_org("Hardening Org B", "hardening-org-b")
    grant = _mk_awarded_grant(org.id, amount_awarded=200.0)
    grant_service.create_budget_line(grant.id, org.id, category="travel", allocated_amount=200.0)

    ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=120.0,
        currency="USD",
        payee="Travel Vendor",
        description="Travel reimbursement",
        grant_id=grant.id,
        expense_category="travel",
    )

    assert "grant.allocation.create" in actions


def test_budget_line_optimistic_lock_rejects_stale_version(ctx):
    org = _mk_org("Hardening Org C", "hardening-org-c")
    grant = _mk_awarded_grant(org.id, amount_awarded=300.0)
    line = grant_service.create_budget_line(grant.id, org.id, category="program", allocated_amount=300.0)
    stale_version = int(line.version_id)

    grant_service.update_budget_line(
        grant.id,
        org.id,
        line.id,
        allocated_amount=280.0,
        expected_version=stale_version,
    )

    with pytest.raises(grant_service.GrantAllocationError, match="version mismatch"):
        grant_service.update_budget_line(
            grant.id,
            org.id,
            line.id,
            allocated_amount=260.0,
            expected_version=stale_version,
        )


def test_allocation_optimistic_lock_and_partial_deallocation(ctx):
    org = _mk_org("Hardening Org D", "hardening-org-d")
    grant = _mk_awarded_grant(org.id, amount_awarded=400.0)
    grant_service.create_budget_line(grant.id, org.id, category="ops", allocated_amount=400.0)

    expense = ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=300.0,
        currency="USD",
        payee="Operations Vendor",
        description="Ops spend",
        grant_id=grant.id,
        expense_category="ops",
    )

    allocation = db.session.scalars(
        db.select(GrantExpenseAllocation).where(GrantExpenseAllocation.expense_id == expense.id).limit(1)
    ).first()
    assert allocation is not None
    stale_version = int(allocation.version_id)

    updated = grant_service.update_allocation(
        grant.id,
        org.id,
        allocation.id,
        amount=250.0,
        expected_version=stale_version,
    )
    assert float(updated.amount) == 250.0

    with pytest.raises(grant_service.GrantAllocationError, match="version mismatch"):
        grant_service.update_allocation(
            grant.id,
            org.id,
            allocation.id,
            amount=240.0,
            expected_version=stale_version,
        )


def test_budget_line_delete_blocked_when_allocations_exist(ctx):
    org = _mk_org("Hardening Org E", "hardening-org-e")
    grant = _mk_awarded_grant(org.id, amount_awarded=500.0)
    line = grant_service.create_budget_line(grant.id, org.id, category="equipment", allocated_amount=500.0)

    ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=120.0,
        currency="USD",
        payee="Equipment Vendor",
        description="Equipment purchase",
        grant_id=grant.id,
        expense_category="equipment",
    )

    with pytest.raises(grant_service.GrantAllocationError, match="existing allocations"):
        grant_service.delete_budget_line(grant.id, org.id, line.id, expected_version=line.version_id)


@pytest.mark.timeout(30)
def test_transaction_boundary_under_simulated_load_rolls_back_failed_allocations(ctx):
    # 20 iterations (budget=10): proves the exact enforcement boundary with minimal DB I/O.
    # The 120-iteration variant tested the same invariant but caused unacceptable CI hang times.
    _BUDGET = 10.0
    _ITERS = 20
    org = _mk_org("Hardening Org F", "hardening-org-f")
    grant = _mk_awarded_grant(org.id, amount_awarded=_BUDGET)
    grant_service.create_budget_line(grant.id, org.id, category="small_ops", allocated_amount=_BUDGET)

    success = 0
    failures = 0
    for i in range(_ITERS):
        try:
            ExpenseService().create_expense(
                org.id,
                project_id=None,
                fund_id=None,
                amount=1.0,
                currency="USD",
                payee=f"Load Vendor {i}",
                description="Load test spend",
                grant_id=grant.id,
                expense_category="small_ops",
            )
            success += 1
        except grant_service.GrantAllocationError:
            failures += 1

    assert success == int(_BUDGET)
    assert failures == _ITERS - int(_BUDGET)

    expense_count = int(
        db.session.scalar(db.select(db.func.count(Expense.id)).where(Expense.organization_id == org.id)) or 0
    )
    allocation_count = int(
        db.session.scalar(
            db.select(db.func.count(GrantExpenseAllocation.id)).where(GrantExpenseAllocation.organization_id == org.id)
        )
        or 0
    )

    assert expense_count == int(_BUDGET)
    assert allocation_count == int(_BUDGET)
