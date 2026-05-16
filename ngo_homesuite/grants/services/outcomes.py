"""Outcomes service adapter for grants domain."""

from ngo_homesuite.services import grant_outcomes_service as _impl


class OutcomesService:
    define_outcome_template = staticmethod(_impl.define_outcome_template)
    record_outcome = staticmethod(_impl.record_outcome)
    outcome_summary = staticmethod(_impl.outcome_summary)
    grant_variance_report = staticmethod(_impl.grant_variance_report)
