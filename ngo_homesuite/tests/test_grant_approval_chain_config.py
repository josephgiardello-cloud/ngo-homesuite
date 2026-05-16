from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ngo_homesuite.models.core import GrantApprovalRequest, Organization, db
from ngo_homesuite.services import grant_service


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def test_per_org_chain_config_amount_branching(shared_test_app):
    with shared_test_app.app_context():
        org = _mk_org("Grant Chain Config Org A", "grant-chain-config-org-a")

        low = grant_service.upsert_approval_chain_config(
            org.id,
            action_type="disbursement_add",
            approver_roles=["finance_admin"],
            required_approvals=1,
            max_amount=500,
            sla_hours=48,
            priority=10,
        )
        high = grant_service.upsert_approval_chain_config(
            org.id,
            action_type="disbursement_add",
            approver_roles=["finance_admin", "controller"],
            required_approvals=2,
            min_amount=500.01,
            sla_hours=24,
            escalation_sla_hours=12,
            priority=20,
        )

        req = grant_service.create_approval_request(
            org.id,
            action_type="disbursement_add",
            resource_type="grant",
            resource_id=99,
            requested_by_user_id=1001,
            requested_by_role="staff",
            payload={"amount": 750.0},
        )

        assert req.required_approvals == 2
        assert sorted(req.approver_roles_json or []) == ["controller", "finance_admin"]
        assert (req.payload_json or {}).get("chain_config_id") == high.id
        assert req.expires_at is not None

        listed = grant_service.list_approval_chain_configs(org.id, action_type="disbursement_add")
        assert [cfg.id for cfg in listed[:2]] == [low.id, high.id]


def test_escalation_sla_queue_escalates_then_expires(shared_test_app):
    with shared_test_app.app_context():
        org = _mk_org("Grant Chain Config Org B", "grant-chain-config-org-b")

        req = grant_service.create_approval_request(
            org.id,
            action_type="proposal_submit",
            resource_type="proposal",
            resource_id=5,
            requested_by_user_id=2001,
            requested_by_role="staff",
            expires_in_hours=1,
            escalation_role="org_admin",
        )

        step1 = grant_service.process_approval_escalation_sla_queue(
            organization_id=org.id,
            now=_now_naive() + timedelta(hours=2),
        )
        assert step1["escalated_count"] == 1
        assert step1["expired_count"] == 0

        escalated = db.session.get(GrantApprovalRequest, req.id)
        assert escalated is not None
        assert escalated.status == "escalated"

        step2 = grant_service.process_approval_escalation_sla_queue(
            organization_id=org.id,
            now=_now_naive() + timedelta(hours=30),
        )
        assert step2["expired_count"] == 1

        expired = db.session.get(GrantApprovalRequest, req.id)
        assert expired is not None
        assert expired.status == "expired"


def test_escalation_role_can_decide_escalated_request(shared_test_app):
    with shared_test_app.app_context():
        org = _mk_org("Grant Chain Config Org C", "grant-chain-config-org-c")

        grant_service.upsert_approval_chain_config(
            org.id,
            action_type="proposal_submit",
            approver_roles=["finance"],
            required_approvals=1,
            escalation_role="org_admin",
            sla_hours=1,
            escalation_sla_hours=24,
            priority=1,
        )

        req = grant_service.create_approval_request(
            org.id,
            action_type="proposal_submit",
            resource_type="proposal",
            resource_id=22,
            requested_by_user_id=3001,
            requested_by_role="staff",
        )

        grant_service.process_approval_escalation_sla_queue(
            organization_id=org.id,
            now=_now_naive() + timedelta(hours=2),
        )

        decided = grant_service.decide_approval_request(
            req.id,
            org.id,
            decided_by_user_id=3002,
            decided_by_role="org_admin",
            decision="approved",
            rationale="Escalation queue handling",
        )
        assert decided.status == "approved"
