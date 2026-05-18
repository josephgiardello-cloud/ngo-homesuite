"""Pre-award service adapter for grants domain."""

from ngo_homesuite.grants.services import preaward_impl as _impl


class PreawardService:
    create_opportunity = staticmethod(_impl.create_opportunity)
    update_opportunity = staticmethod(_impl.update_opportunity)
    list_opportunities = staticmethod(_impl.list_opportunities)
    create_proposal = staticmethod(_impl.create_proposal)
    submit_proposal = staticmethod(_impl.submit_proposal)
    set_proposal_outcome = staticmethod(_impl.set_proposal_outcome)
    convert_opportunity_to_grant = staticmethod(_impl.convert_opportunity_to_grant)
    opportunity_forecast_summary = staticmethod(_impl.opportunity_forecast_summary)
    search_applicable_opportunities = staticmethod(_impl.search_applicable_opportunities)
    generate_proposal_compliance_guidance = staticmethod(_impl.generate_proposal_compliance_guidance)
    generate_proposal_draft_assist = staticmethod(_impl.generate_proposal_draft_assist)
    get_opportunity_ai_context = staticmethod(_impl.get_opportunity_ai_context)
    ingest_opportunity_guidance = staticmethod(_impl.ingest_opportunity_guidance)
    save_draft_assist_as_proposal = staticmethod(_impl.save_draft_assist_as_proposal)
