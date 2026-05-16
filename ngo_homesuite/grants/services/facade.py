"""Thin grants facade orchestrating grant sub-services."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from ngo_homesuite.grants.services.accounting import AccountingService
from ngo_homesuite.grants.services.approval import ApprovalService
from ngo_homesuite.grants.services.outcomes import OutcomesService
from ngo_homesuite.grants.services.preaward import PreawardService
from ngo_homesuite.services import grant_service as _grant_lifecycle


class GrantsFacade:
    """Single grant-domain entrypoint for routes and cross-domain callers."""

    def __init__(self) -> None:
        self.preaward = PreawardService()
        self.outcomes = OutcomesService()
        self.approval = ApprovalService()
        self.accounting = AccountingService()

    # Lifecycle + financial façade methods
    create_grant = staticmethod(_grant_lifecycle.create_grant)
    list_grants = staticmethod(_grant_lifecycle.list_grants)
    get_grant = staticmethod(_grant_lifecycle.get_grant)
    update_grant = staticmethod(_grant_lifecycle.update_grant)
    delete_grant = staticmethod(_grant_lifecycle.delete_grant)
    advance_grant_status = staticmethod(_grant_lifecycle.advance_grant_status)
    add_disbursement = staticmethod(_grant_lifecycle.add_disbursement)
    get_disbursements = staticmethod(_grant_lifecycle.get_disbursements)
    create_budget_line = staticmethod(_grant_lifecycle.create_budget_line)
    update_budget_line = staticmethod(_grant_lifecycle.update_budget_line)
    delete_budget_line = staticmethod(_grant_lifecycle.delete_budget_line)
    allocate_expense_to_budget_line = staticmethod(_grant_lifecycle.allocate_expense_to_budget_line)
    update_allocation = staticmethod(_grant_lifecycle.update_allocation)
    delete_allocation = staticmethod(_grant_lifecycle.delete_allocation)

    # Pre-award façade methods
    create_opportunity = staticmethod(PreawardService.create_opportunity)
    update_opportunity = staticmethod(PreawardService.update_opportunity)
    list_opportunities = staticmethod(PreawardService.list_opportunities)
    create_proposal = staticmethod(PreawardService.create_proposal)
    submit_proposal = staticmethod(PreawardService.submit_proposal)
    set_proposal_outcome = staticmethod(PreawardService.set_proposal_outcome)
    convert_opportunity_to_grant = staticmethod(_grant_lifecycle.convert_opportunity_to_grant)
    opportunity_forecast_summary = staticmethod(PreawardService.opportunity_forecast_summary)

    # Approval façade methods
    create_approval_request = staticmethod(ApprovalService.create_approval_request)
    decide_approval_request = staticmethod(ApprovalService.decide_approval_request)
    list_approval_chain_configs = staticmethod(ApprovalService.list_chain_configs)
    upsert_approval_chain_config = staticmethod(ApprovalService.upsert_chain_config)
    disable_approval_chain_config = staticmethod(ApprovalService.disable_chain_config)
    process_approval_escalation_sla_queue = staticmethod(ApprovalService.process_escalation_sla_queue)
    escalate_expired_approval_requests = staticmethod(_grant_lifecycle.escalate_expired_approval_requests)

    # Approval-gated orchestrations
    submit_proposal_with_approval = staticmethod(_grant_lifecycle.submit_proposal_with_approval)
    add_disbursement_with_approval = staticmethod(_grant_lifecycle.add_disbursement_with_approval)
    record_outcome_with_approval = staticmethod(_grant_lifecycle.record_outcome_with_approval)
    close_grant_with_approval = staticmethod(_grant_lifecycle.close_grant_with_approval)

    # Outcomes and accounting summaries
    grant_accounting_snapshot = staticmethod(_grant_lifecycle.grant_accounting_snapshot)
    restricted_funding_summary = staticmethod(_grant_lifecycle.restricted_funding_summary)
    grant_pipeline_summary = staticmethod(_grant_lifecycle.grant_pipeline_summary)
    grant_calendar_events = staticmethod(_grant_lifecycle.grant_calendar_events)


def get_grants_facade() -> GrantsFacade:
    """Factory to standardize facade creation at call-sites."""
    return GrantsFacade()
