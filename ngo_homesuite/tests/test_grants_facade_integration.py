from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ngo_homesuite.grants.services import GrantsFacade
from ngo_homesuite.models.core import Organization, db


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def test_full_grant_lifecycle_via_facade_only(shared_test_app):
    with shared_test_app.app_context():
        facade = GrantsFacade()
        org = _mk_org("Grant Facade Org A", "grant-facade-org-a")

        facade.upsert_approval_chain_config(
            org.id,
            action_type="disbursement_add",
            approver_roles=["finance_admin", "controller"],
            required_approvals=2,
            min_amount=500,
            escalation_role="org_admin",
            sla_hours=24,
            escalation_sla_hours=12,
            priority=1,
        )

        opp = facade.create_opportunity(
            organization_id=org.id,
            funder_name="Facade Funder",
            program_name="Maine Program",
            title="Facade Grant",
            amount_min=900.0,
            amount_max=1100.0,
            probability=0.8,
        )
        proposal = facade.create_proposal(
            opp.id,
            org.id,
            amount_requested=1000.0,
            narrative_summary="Narrative",
            document_ref="proposal.pdf",
        )

        submit_req = facade.create_approval_request(
            org.id,
            action_type="proposal_submit",
            resource_type="proposal",
            resource_id=proposal.id,
            requested_by_user_id=101,
            requested_by_role="staff",
            expires_in_hours=2,
        )
        facade.decide_approval_request(
            submit_req.id,
            org.id,
            decided_by_user_id=201,
            decided_by_role="finance",
            decision="approved",
            rationale="Initial submission approved",
        )
        facade.submit_proposal_with_approval(
            proposal.id,
            org.id,
            submission_date=date(2026, 9, 1),
            approval_request_id=submit_req.id,
            executed_by_user_id=301,
        )

        facade.set_proposal_outcome(proposal.id, org.id, outcome="awarded")
        grant = facade.convert_opportunity_to_grant(
            opp.id,
            org.id,
            amount_awarded=1000.0,
            award_date=date(2026, 10, 1),
        )

        facade.create_budget_line(grant.id, org.id, category="direct_services", allocated_amount=1000.0)

        disb_req = facade.create_approval_request(
            org.id,
            action_type="disbursement_add",
            resource_type="grant",
            resource_id=grant.id,
            requested_by_user_id=102,
            requested_by_role="staff",
            payload={"amount": 1000.0},
            expires_at=_now_naive() + timedelta(hours=1),
        )
        facade.decide_approval_request(
            disb_req.id,
            org.id,
            decided_by_user_id=202,
            decided_by_role="finance_admin",
            decision="approved",
            rationale="Finance approval",
        )
        facade.decide_approval_request(
            disb_req.id,
            org.id,
            decided_by_user_id=203,
            decided_by_role="controller",
            decision="approved",
            rationale="Controller approval",
        )

        disb = facade.add_disbursement_with_approval(
            grant.id,
            org.id,
            amount=1000.0,
            received_date=date(2026, 10, 15),
            approval_request_id=disb_req.id,
            executed_by_user_id=302,
        )
        assert float(disb.amount) == 1000.0

        queue_result = facade.process_approval_escalation_sla_queue(organization_id=org.id, now=_now_naive())
        assert queue_result["processed_org_count"] >= 0
