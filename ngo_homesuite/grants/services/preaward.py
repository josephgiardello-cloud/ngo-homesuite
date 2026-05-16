"""Pre-award service adapter for grants domain."""

from ngo_homesuite.grants.services import preaward_impl as _impl


class PreawardService:
    create_opportunity = staticmethod(_impl.create_opportunity)
    update_opportunity = staticmethod(_impl.update_opportunity)
    list_opportunities = staticmethod(_impl.list_opportunities)
    calibrate_external_opportunity = staticmethod(_impl.calibrate_external_opportunity)
    import_external_opportunity = staticmethod(_impl.import_external_opportunity)
    import_grants_gov_opportunities = staticmethod(_impl.import_grants_gov_opportunities)
    create_proposal = staticmethod(_impl.create_proposal)
    submit_proposal = staticmethod(_impl.submit_proposal)
    set_proposal_outcome = staticmethod(_impl.set_proposal_outcome)
    convert_opportunity_to_grant = staticmethod(_impl.convert_opportunity_to_grant)
    opportunity_forecast_summary = staticmethod(_impl.opportunity_forecast_summary)
