"""Grant-domain exception definitions."""

from ngo_homesuite.grants.services import accounting_policy_impl
from ngo_homesuite.grants.services import approval_impl


class GrantNotFound(Exception):
    """Raised when a grant cannot be found for the given org."""

    def __init__(self, grant_id: int):
        super().__init__(f"Grant {grant_id} not found.")
        self.grant_id = grant_id


class InvalidGrantTransition(ValueError):
    """Raised when a requested grant status transition is not permitted."""


class GrantAllocationError(ValueError):
    """Raised when grant allocation violates budget or tenant constraints."""


GrantApprovalError = approval_impl.GrantApprovalError
GrantAccountingPolicyError = accounting_policy_impl.GrantAccountingPolicyError

__all__ = [
    "GrantNotFound",
    "InvalidGrantTransition",
    "GrantAllocationError",
    "GrantApprovalError",
    "GrantAccountingPolicyError",
]
