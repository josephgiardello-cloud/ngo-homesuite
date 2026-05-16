"""Accounting policy service adapter for grants domain."""

from ngo_homesuite.services import grant_accounting_policy_service as _impl


class AccountingService:
    evaluate_allowable_cost = staticmethod(_impl.evaluate_allowable_cost)
    enforce_allowable_cost = staticmethod(_impl.enforce_allowable_cost)
    compute_multi_year_carry_forward = staticmethod(_impl.compute_multi_year_carry_forward)
    compute_indirect_cost_pool = staticmethod(_impl.compute_indirect_cost_pool)
