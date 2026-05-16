from __future__ import annotations

from datetime import datetime

import pytest

from ngo_homesuite.models.core import Expense, GrantDisbursement, Organization, db
from ngo_homesuite.services import grant_accounting_policy_service, grant_service


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


def test_allowable_cost_policy_blocks_unallowable_keyword(ctx):
    org = _mk_org("Grant Accounting Org A", "grant-accounting-org-a")
    grant = grant_service.create_grant(
        organization_id=org.id,
        funder_name="Policy Funder",
        title="Policy Grant",
        amount_requested=1000,
    )
    grant_service.advance_grant_status(grant.id, org.id, new_status="submitted")
    grant_service.advance_grant_status(grant.id, org.id, new_status="awarded", amount_awarded=1000)

    grant_service.create_budget_line(grant.id, org.id, category="direct_services", allocated_amount=1000)

    expense = Expense(
        organization_id=org.id,
        amount=100,
        currency="USD",
        payee="Vendor",
        description="Alcohol reimbursement",
        paid_at=datetime(2026, 1, 15),
    )
    db.session.add(expense)
    db.session.commit()

    with pytest.raises(grant_accounting_policy_service.GrantAccountingPolicyError, match="unallowable"):
        grant_service.allocate_expense_to_budget_line(
            grant.id,
            org.id,
            expense_id=expense.id,
            category="direct_services",
        )


def test_carry_forward_and_indirect_pool_foundations(ctx):
    org = _mk_org("Grant Accounting Org B", "grant-accounting-org-b")
    grant = grant_service.create_grant(
        organization_id=org.id,
        funder_name="Accounting Funder",
        title="Accounting Grant",
        amount_requested=1000,
    )
    grant_service.advance_grant_status(grant.id, org.id, new_status="submitted")
    grant_service.advance_grant_status(grant.id, org.id, new_status="awarded", amount_awarded=1000)

    db.session.add(
        GrantDisbursement(
            grant_id=grant.id,
            organization_id=org.id,
            amount=1000,
            received_date=datetime(2026, 1, 10).date(),
            currency="USD",
        )
    )
    db.session.commit()

    grant_service.create_budget_line(grant.id, org.id, category="direct_services", allocated_amount=700)
    grant_service.create_budget_line(grant.id, org.id, category="operations", allocated_amount=300)

    expense_2026 = Expense(
        organization_id=org.id,
        amount=600,
        currency="USD",
        payee="Direct Services Vendor",
        description="Service delivery",
        paid_at=datetime(2026, 6, 1),
    )
    expense_2027 = Expense(
        organization_id=org.id,
        amount=200,
        currency="USD",
        payee="Ops Vendor",
        description="Program ops",
        paid_at=datetime(2027, 1, 15),
    )
    db.session.add(expense_2026)
    db.session.add(expense_2027)
    db.session.commit()

    grant_service.allocate_expense_to_budget_line(
        grant.id,
        org.id,
        expense_id=expense_2026.id,
        category="direct_services",
    )
    grant_service.allocate_expense_to_budget_line(
        grant.id,
        org.id,
        expense_id=expense_2027.id,
        category="operations",
    )

    snapshot = grant_service.grant_accounting_snapshot(grant.id, org.id, indirect_rate=0.1)

    carry_forward = snapshot["carry_forward"]
    assert carry_forward[0]["year"] == 2026
    assert carry_forward[0]["carry_forward"] == pytest.approx(400.0)
    assert carry_forward[1]["year"] == 2027
    assert carry_forward[1]["carry_forward"] == pytest.approx(200.0)

    indirect_pool = snapshot["indirect_pool"]
    assert indirect_pool["direct_cost_base"] == pytest.approx(800.0)
    assert indirect_pool["calculated_indirect_pool"] == pytest.approx(80.0)
