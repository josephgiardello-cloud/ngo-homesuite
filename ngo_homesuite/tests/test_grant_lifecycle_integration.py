from __future__ import annotations

from datetime import date

import pytest

from ngo_homesuite.models.core import GrantApprovalRequest, Organization, db
from ngo_homesuite.services import grant_outcomes_service, grant_service
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


def test_full_grant_lifecycle_integration_with_approvals(ctx):
    org = _mk_org("Grant Lifecycle Org A", "grant-lifecycle-org-a")

    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Lifecycle Funder",
        program_name="Maine Community Program",
        title="Integrated Grant Flow",
        amount_min=900.0,
        amount_max=1100.0,
        probability=0.7,
    )

    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        amount_requested=1000.0,
        narrative_summary="Complete proposal narrative",
        document_ref="proposal-v1.pdf",
    )

    submit_req = grant_service.create_approval_request(
        org.id,
        action_type="proposal_submit",
        resource_type="proposal",
        resource_id=proposal.id,
        requested_by_user_id=101,
        requested_by_role="staff",
        payload={"reason": "Ready for submission"},
    )
    grant_service.decide_approval_request(
        submit_req.id,
        org.id,
        decided_by_user_id=202,
        decided_by_role="finance",
        decision="approved",
        comment="Submission approved",
    )

    submitted = grant_service.submit_proposal_with_approval(
        proposal.id,
        org.id,
        submission_date=date(2026, 7, 1),
        approval_request_id=submit_req.id,
        executed_by_user_id=303,
    )
    assert submitted.outcome == "submitted"

    grant_service.set_proposal_outcome(submitted.id, org.id, outcome="awarded")

    grant = grant_service.convert_opportunity_to_grant(
        opp.id,
        org.id,
        amount_awarded=1000.0,
        award_date=date(2026, 8, 1),
    )
    assert grant.status == "awarded"

    grant_service.create_budget_line(grant.id, org.id, category="direct_services", allocated_amount=600.0)
    grant_service.create_budget_line(grant.id, org.id, category="operations", allocated_amount=400.0)

    disb_req = grant_service.create_approval_request(
        org.id,
        action_type="disbursement_add",
        resource_type="grant",
        resource_id=grant.id,
        requested_by_user_id=101,
        requested_by_role="staff",
    )
    grant_service.decide_approval_request(
        disb_req.id,
        org.id,
        decided_by_user_id=202,
        decided_by_role="finance_admin",
        decision="approved",
    )

    disb = grant_service.add_disbursement_with_approval(
        grant.id,
        org.id,
        amount=1000.0,
        received_date=date(2026, 8, 15),
        approval_request_id=disb_req.id,
        executed_by_user_id=303,
    )
    assert float(disb.amount) == 1000.0

    ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=600.0,
        currency="USD",
        payee="Direct Services Vendor",
        description="Direct services expense",
        grant_id=grant.id,
        expense_category="direct_services",
    )
    ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=400.0,
        currency="USD",
        payee="Ops Vendor",
        description="Operations expense",
        grant_id=grant.id,
        expense_category="operations",
    )

    template = grant_outcomes_service.define_outcome_template(
        grant.id,
        org.id,
        metric_name="Households Stabilized",
        target_value=50,
        baseline_value=10,
        unit="households",
    )

    outcome_req = grant_service.create_approval_request(
        org.id,
        action_type="outcome_record",
        resource_type="grant",
        resource_id=grant.id,
        requested_by_user_id=101,
        requested_by_role="staff",
    )
    grant_service.decide_approval_request(
        outcome_req.id,
        org.id,
        decided_by_user_id=202,
        decided_by_role="executive",
        decision="approved",
    )

    grant_service.record_outcome_with_approval(
        grant.id,
        org.id,
        template_id=template.id,
        current_value=45,
        approval_request_id=outcome_req.id,
        executed_by_user_id=303,
        note="Q4 results",
    )

    close_req = grant_service.create_approval_request(
        org.id,
        action_type="grant_closeout",
        resource_type="grant",
        resource_id=grant.id,
        requested_by_user_id=101,
        requested_by_role="staff",
    )
    grant_service.decide_approval_request(
        close_req.id,
        org.id,
        decided_by_user_id=202,
        decided_by_role="org_admin",
        decision="approved",
    )

    closed = grant_service.close_grant_with_approval(
        grant.id,
        org.id,
        approval_request_id=close_req.id,
        executed_by_user_id=303,
    )
    assert closed.status == "closed"

    approvals = list(
        db.session.scalars(
            db.select(GrantApprovalRequest).where(GrantApprovalRequest.organization_id == org.id)
        )
    )
    assert len(approvals) >= 4
    assert all(item.status == "executed" for item in approvals)


def test_self_approval_is_blocked(ctx):
    org = _mk_org("Grant Lifecycle Org B", "grant-lifecycle-org-b")

    req = grant_service.create_approval_request(
        org.id,
        action_type="disbursement_add",
        resource_type="grant",
        resource_id=1,
        requested_by_user_id=9001,
        requested_by_role="staff",
    )

    with pytest.raises(grant_service.GrantApprovalError, match="cannot approve"):
        grant_service.decide_approval_request(
            req.id,
            org.id,
            decided_by_user_id=9001,
            decided_by_role="finance",
            decision="approved",
        )


def test_cannot_execute_grant_action_without_approved_request(ctx):
    org = _mk_org("Grant Lifecycle Org C", "grant-lifecycle-org-c")
    grant = grant_service.create_grant(
        organization_id=org.id,
        funder_name="Flow Funder",
        title="Flow Grant",
        amount_requested=100,
    )
    grant_service.advance_grant_status(grant.id, org.id, new_status="submitted")
    grant_service.advance_grant_status(grant.id, org.id, new_status="awarded", amount_awarded=100)

    req = grant_service.create_approval_request(
        org.id,
        action_type="disbursement_add",
        resource_type="grant",
        resource_id=grant.id,
        requested_by_user_id=42,
        requested_by_role="staff",
    )

    with pytest.raises(grant_service.GrantApprovalError, match="must be approved"):
        grant_service.add_disbursement_with_approval(
            grant.id,
            org.id,
            amount=100,
            received_date=date(2026, 5, 16),
            approval_request_id=req.id,
            executed_by_user_id=43,
        )
