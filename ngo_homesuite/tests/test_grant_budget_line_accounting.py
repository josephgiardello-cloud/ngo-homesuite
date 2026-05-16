from __future__ import annotations

from datetime import date

import pytest

from ngo_homesuite.grants.exceptions import GrantAllocationError
from ngo_homesuite.grants.exceptions import InvalidGrantTransition
from ngo_homesuite.grants.facade import GrantsFacade
from ngo_homesuite.models.core import Organization, db
from ngo_homesuite.services.expense_service import ExpenseService


grant_service = GrantsFacade()


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
        funder_name="Budget Funder",
        title="Restricted Budget Grant",
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


def test_budget_lines_and_allocation_flow_enforces_line_balance(ctx):
    org = _mk_org("Budget Line Org A", "budget-line-org-a")
    grant = _mk_awarded_grant(org.id, amount_awarded=1000.0)

    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="program_services",
        allocated_amount=700.0,
    )
    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="admin",
        allocated_amount=300.0,
    )

    expense = ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=650.0,
        currency="USD",
        payee="Program Vendor",
        description="Program services spending",
        grant_id=grant.id,
        expense_category="program_services",
    )
    assert expense.id is not None

    with pytest.raises(GrantAllocationError, match="remaining budget line balance"):
        ExpenseService().create_expense(
            org.id,
            project_id=None,
            fund_id=None,
            amount=100.0,
            currency="USD",
            payee="Program Vendor 2",
            description="Would exceed line",
            grant_id=grant.id,
            expense_category="program_services",
        )


def test_allocation_rejects_unknown_category(ctx):
    org = _mk_org("Budget Line Org B", "budget-line-org-b")
    grant = _mk_awarded_grant(org.id, amount_awarded=500.0)

    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="equipment",
        allocated_amount=500.0,
    )

    with pytest.raises(GrantAllocationError, match="no budget line configured"):
        ExpenseService().create_expense(
            org.id,
            project_id=None,
            fund_id=None,
            amount=100.0,
            currency="USD",
            payee="Unknown Category Vendor",
            description="Invalid category allocation",
            grant_id=grant.id,
            expense_category="travel",
        )


def test_closeout_with_budget_lines_requires_zero_remaining(ctx):
    org = _mk_org("Budget Line Org C", "budget-line-org-c")
    grant = _mk_awarded_grant(org.id, amount_awarded=900.0)

    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="direct_services",
        allocated_amount=600.0,
    )
    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="evaluation",
        allocated_amount=300.0,
    )

    ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=600.0,
        currency="USD",
        payee="Direct Services Partner",
        description="Direct services",
        grant_id=grant.id,
        expense_category="direct_services",
    )

    with pytest.raises(InvalidGrantTransition, match="outstanding restricted balance"):
        grant_service.advance_grant_status(grant.id, org.id, new_status="closed")

    ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=300.0,
        currency="USD",
        payee="Evaluation Partner",
        description="Evaluation",
        grant_id=grant.id,
        expense_category="evaluation",
    )

    closed = grant_service.advance_grant_status(grant.id, org.id, new_status="closed")
    assert closed.status == "closed"


def test_cross_tenant_expense_allocation_is_blocked(ctx):
    org_a = _mk_org("Budget Line Org D1", "budget-line-org-d1")
    org_b = _mk_org("Budget Line Org D2", "budget-line-org-d2")

    grant = _mk_awarded_grant(org_a.id, amount_awarded=200.0)
    grant_service.create_budget_line(
        grant.id,
        org_a.id,
        category="supplies",
        allocated_amount=200.0,
    )

    expense = ExpenseService().create_expense(
        org_b.id,
        project_id=None,
        fund_id=None,
        amount=50.0,
        currency="USD",
        payee="Cross Tenant Vendor",
        description="Cross-tenant allocation test",
    )

    with pytest.raises(GrantAllocationError, match="expense not found"):
        grant_service.allocate_expense_to_budget_line(
            grant_id=grant.id,
            organization_id=org_a.id,
            expense_id=expense.id,
            category="supplies",
        )
