"""Approval service adapter for grants domain."""

from ngo_homesuite.services import grant_approval_service as _impl


class ApprovalService:
    create_approval_request = staticmethod(_impl.create_approval_request)
    decide_approval_request = staticmethod(_impl.decide_approval_request)
    consume_approved_request = staticmethod(_impl.consume_approved_request)
    list_chain_configs = staticmethod(_impl.list_chain_configs)
    upsert_chain_config = staticmethod(_impl.upsert_chain_config)
    disable_chain_config = staticmethod(_impl.disable_chain_config)
    process_escalation_sla_queue = staticmethod(_impl.process_escalation_sla_queue)
