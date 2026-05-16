"""Grant-domain value types and enums."""

from enum import Enum


class GrantApprovalAction(str, Enum):
    PROPOSAL_SUBMIT = "proposal_submit"
    DISBURSEMENT_ADD = "disbursement_add"
    OUTCOME_RECORD = "outcome_record"
    GRANT_CLOSEOUT = "grant_closeout"


__all__ = ["GrantApprovalAction"]
