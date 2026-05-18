from __future__ import annotations

from datetime import date

import pytest

from ngo_homesuite.grants.facade import GrantsFacade
from ngo_homesuite.models.core import GrantOpportunity, GrantProposal, Organization, db


grant_service = GrantsFacade()


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield


def _mk_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def test_create_opportunity_computes_probability_weighted_amount(ctx):
    org = _mk_org("PreAward Org A", "preaward-org-a")

    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Maine Foundation",
        program_name="Community Impact",
        title="After School Program",
        deadline=date(2026, 7, 1),
        amount_min=50000,
        amount_max=70000,
        probability=0.25,
    )

    assert float(opp.probability_weighted_amount) == 15000.0


def test_create_opportunity_rejects_invalid_probability(ctx):
    org = _mk_org("PreAward Org B", "preaward-org-b")

    with pytest.raises(ValueError, match="probability must be between 0 and 1"):
        grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Maine Foundation",
            program_name="Community Impact",
            title="Bad Probability",
            probability=1.5,
        )


def test_create_opportunity_rejects_invalid_amount_range(ctx):
    org = _mk_org("PreAward Org C", "preaward-org-c")

    with pytest.raises(ValueError, match="amount_max must be greater"):
        grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Maine Foundation",
            program_name="Community Impact",
            title="Bad Range",
            amount_min=30000,
            amount_max=20000,
            probability=0.4,
        )


def test_create_proposal_versions_increment_per_opportunity(ctx):
    org = _mk_org("PreAward Org D", "preaward-org-d")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="State Funder",
        program_name="Health",
        title="Mobile Clinic",
    )

    p1 = grant_service.create_proposal(opp.id, org.id, narrative_summary="v1")
    p2 = grant_service.create_proposal(opp.id, org.id, narrative_summary="v2")

    assert int(p1.version_number) == 1
    assert int(p2.version_number) == 2


def test_submit_proposal_requires_narrative(ctx):
    org = _mk_org("PreAward Org E", "preaward-org-e")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder E",
        program_name="Housing",
        title="Narrative Missing",
    )
    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        amount_requested=25000,
        document_ref="proposal_v1.pdf",
    )

    with pytest.raises(ValueError, match="narrative_summary"):
        grant_service.submit_proposal(proposal.id, org.id, submission_date=date(2026, 6, 1))


def test_submit_proposal_requires_amount_requested(ctx):
    org = _mk_org("PreAward Org F", "preaward-org-f")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder F",
        program_name="Education",
        title="Amount Missing",
    )
    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        narrative_summary="Detailed narrative",
        document_ref="proposal_v1.pdf",
    )

    with pytest.raises(ValueError, match="amount_requested"):
        grant_service.submit_proposal(proposal.id, org.id, submission_date=date(2026, 6, 2))


def test_submit_proposal_requires_document_ref(ctx):
    org = _mk_org("PreAward Org G", "preaward-org-g")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder G",
        program_name="Workforce",
        title="Document Missing",
    )
    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        narrative_summary="Strong narrative",
        amount_requested=18000,
    )

    with pytest.raises(ValueError, match="document_ref"):
        grant_service.submit_proposal(proposal.id, org.id, submission_date=date(2026, 6, 3))


def test_submit_proposal_updates_proposal_and_opportunity_status(ctx):
    org = _mk_org("PreAward Org H", "preaward-org-h")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder H",
        program_name="Public Health",
        title="Submit Success",
    )
    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        narrative_summary="Compliant narrative",
        amount_requested=45000,
        document_ref="submit_success_v1.pdf",
    )

    submitted = grant_service.submit_proposal(
        proposal.id,
        org.id,
        submission_date=date(2026, 6, 10),
    )

    assert submitted.outcome == "submitted"
    refreshed_opp = db.session.get(GrantOpportunity, opp.id)
    assert refreshed_opp is not None
    assert refreshed_opp.status == "submitted"


def test_set_proposal_outcome_awarded_marks_opportunity_awarded(ctx):
    org = _mk_org("PreAward Org I", "preaward-org-i")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder I",
        program_name="Youth",
        title="Outcome Award",
    )
    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        narrative_summary="Award-ready narrative",
        amount_requested=60000,
        document_ref="award_v1.pdf",
    )
    grant_service.submit_proposal(proposal.id, org.id, submission_date=date(2026, 6, 20))

    result = grant_service.set_proposal_outcome(proposal.id, org.id, outcome="awarded")
    assert result.outcome == "awarded"
    refreshed_opp = db.session.get(GrantOpportunity, opp.id)
    assert refreshed_opp is not None
    assert refreshed_opp.status == "awarded"


def test_convert_opportunity_to_grant_links_awarded_grant(ctx):
    org = _mk_org("PreAward Org J", "preaward-org-j")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder J",
        program_name="Food Security",
        title="Convert Opportunity",
        amount_min=20000,
        amount_max=30000,
        probability=0.6,
    )

    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        narrative_summary="Award-track narrative",
        amount_requested=26000,
        document_ref="convert_v1.pdf",
    )
    grant_service.submit_proposal(proposal.id, org.id, submission_date=date(2026, 7, 20))
    grant_service.set_proposal_outcome(proposal.id, org.id, outcome="awarded")

    grant = grant_service.convert_opportunity_to_grant(
        opp.id,
        org.id,
        amount_awarded=25000,
        award_date=date(2026, 8, 1),
    )

    refreshed_opp = db.session.get(GrantOpportunity, opp.id)
    assert refreshed_opp is not None
    assert refreshed_opp.status == "awarded"
    assert int(refreshed_opp.awarded_grant_id or 0) == int(grant.id)

    def test_ingest_opportunity_guidance_appends_normalized_requirements_to_notes(ctx):
        org = _mk_org("PreAward Org Guidance", "preaward-org-guidance")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Funder Guidance",
            program_name="Community Health",
            title="Guideline Ingestion",
            notes="Existing note",
        )

        result = grant_service.ingest_opportunity_guidance(
            opp.id,
            org.id,
            guideline_text="Applicants must include evaluation metrics and shall file semiannual compliance updates.",
            source_name="guidelines.pdf",
            merge_into_notes=True,
        )

        refreshed_opp = db.session.get(GrantOpportunity, opp.id)
        assert refreshed_opp is not None
        assert result["requirement_count"] >= 1
        assert "Guideline source: guidelines.pdf" in str(refreshed_opp.notes)
        assert "Extracted compliance requirements" in str(refreshed_opp.notes)

    def test_save_draft_assist_as_proposal_creates_versioned_proposal(ctx):
        org = _mk_org("PreAward Org DraftSave", "preaward-org-draftsave")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Funder DraftSave",
            program_name="Housing Stability",
            title="Draft Save",
            notes="Applicants must include measurable outcomes and shall maintain quarterly reporting.",
        )

        proposal = grant_service.save_draft_assist_as_proposal(
            opp.id,
            org.id,
            organization_summary="We operate housing stabilization services across the county.",
            program_summary="The program combines case management, landlord mediation, and short-term rental support.",
            applicant_profile="Housing stability outcomes for families",
            amount_requested=45000,
            existing_draft="We track outcomes and maintain a reporting calendar.",
            document_ref="draft-save.md",
        )

        assert int(proposal.version_number) == 1
        assert proposal.document_ref == "draft-save.md"
        assert "Eligibility And Mission Fit" in str(proposal.narrative_summary)
        assert "Draft assist trace" in str(proposal.notes)

        refreshed = db.session.get(GrantProposal, proposal.id)
        assert refreshed is not None


def test_set_proposal_outcome_awarded_requires_submitted_state(ctx):
    org = _mk_org("PreAward Org M", "preaward-org-m")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder M",
        program_name="Program M",
        title="Outcome State Gate",
    )
    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        narrative_summary="Narrative M",
        amount_requested=10000,
        document_ref="m_v1.pdf",
    )

    with pytest.raises(ValueError, match="from submitted"):
        grant_service.set_proposal_outcome(proposal.id, org.id, outcome="awarded")


def test_convert_opportunity_to_grant_requires_awarded_opportunity_and_awarded_proposal(ctx):
    org = _mk_org("PreAward Org N", "preaward-org-n")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder N",
        program_name="Program N",
        title="Convert Gate",
        amount_min=9000,
        amount_max=11000,
        probability=0.4,
    )
    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        narrative_summary="Narrative N",
        amount_requested=10000,
        document_ref="n_v1.pdf",
    )
    grant_service.submit_proposal(proposal.id, org.id, submission_date=date(2026, 8, 5))
    grant_service.set_proposal_outcome(proposal.id, org.id, outcome="declined")

    with pytest.raises(ValueError, match="must be in awarded status"):
        grant_service.convert_opportunity_to_grant(
            opp.id,
            org.id,
            amount_awarded=10000,
            award_date=date(2026, 9, 1),
        )


def test_convert_opportunity_to_grant_blocks_second_conversion(ctx):
    org = _mk_org("PreAward Org O", "preaward-org-o")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder O",
        program_name="Program O",
        title="Double Convert Guard",
        amount_min=15000,
        amount_max=17000,
        probability=0.7,
    )
    proposal = grant_service.create_proposal(
        opp.id,
        org.id,
        narrative_summary="Narrative O",
        amount_requested=16000,
        document_ref="o_v1.pdf",
    )
    grant_service.submit_proposal(proposal.id, org.id, submission_date=date(2026, 8, 10))
    grant_service.set_proposal_outcome(proposal.id, org.id, outcome="awarded")

    grant_service.convert_opportunity_to_grant(
        opp.id,
        org.id,
        amount_awarded=16000,
        award_date=date(2026, 9, 10),
    )

    with pytest.raises(ValueError, match="already linked"):
        grant_service.convert_opportunity_to_grant(
            opp.id,
            org.id,
            amount_awarded=16000,
            award_date=date(2026, 9, 11),
        )


def test_opportunity_forecast_summary_includes_active_pipeline_only(ctx):
    org = _mk_org("PreAward Org K", "preaward-org-k")

    grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder K1",
        program_name="Program K1",
        title="Active 1",
        amount_min=10000,
        amount_max=20000,
        probability=0.5,
        status="qualified",
    )
    grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder K2",
        program_name="Program K2",
        title="Active 2",
        amount_min=30000,
        amount_max=30000,
        probability=0.25,
        status="submitted",
    )
    grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Funder K3",
        program_name="Program K3",
        title="Declined",
        amount_min=99999,
        amount_max=99999,
        probability=1.0,
        status="declined",
    )

    summary = grant_service.opportunity_forecast_summary(org.id)
    assert summary["pipeline_count"] == 2
    assert summary["pipeline_amount"] == 45000.0
    assert summary["probability_weighted_amount"] == 15000.0


def test_opportunity_and_proposal_are_tenant_scoped(ctx):
    org_a = _mk_org("PreAward Org L1", "preaward-org-l1")
    org_b = _mk_org("PreAward Org L2", "preaward-org-l2")

    opp_a = grant_service.create_opportunity(
        organization_id=org_a.id,
        funder_name="Funder L",
        program_name="Program L",
        title="Tenant Scope",
    )
    grant_service.create_proposal(
        opp_a.id,
        org_a.id,
        narrative_summary="Tenant scoped",
        amount_requested=12000,
        document_ref="tenant_scope_v1.pdf",
    )

    listed_b = grant_service.list_opportunities(org_b.id)
    assert all(int(item.organization_id) == int(org_b.id) for item in listed_b)

    proposal_b = db.session.scalars(
        db.select(GrantProposal).where(GrantProposal.organization_id == org_b.id)
    ).all()
    assert proposal_b == []


def test_external_opportunity_ai_context_exposes_requirements_and_guidance(ctx):
    org = _mk_org("PreAward Org External", "preaward-org-external")
    opp = grant_service.create_opportunity(
        organization_id=org.id,
        funder_name="Grants.gov",
        program_name="Federal Housing Stabilization",
        title="Housing Stabilization Federal Opportunity",
        external_source="grants_gov",
        external_opportunity_id="GRANT-EXT-001",
        external_url="https://www.grants.gov/search-results-detail/GRANT-EXT-001",
        external_details_json={
            "summary": "Federal support for housing stabilization and eviction prevention.",
            "eligibility": ["501(c)(3) nonprofits", "Units of local government"],
            "disqualifications": ["For-profit entities are not eligible"],
            "application_guidance": ["Use evidence-backed housing outcomes", "Include implementation timeline"],
            "applicable_conditions": ["Quarterly federal reporting required"],
            "requirements": ["Submit SAM registration", "Provide logic model"],
            "categories": ["Housing", "Federal Assistance"],
        },
        notes="Applicants must provide logic model and quarterly reporting plan.",
    )

    context = grant_service.get_opportunity_ai_context(opp.id, org.id)

    assert int(context["opportunity_id"]) == int(opp.id)
    assert context["external_source"] == "grants_gov"
    assert "501(c)(3) nonprofits" in context["eligibility"]
    assert "For-profit entities are not eligible" in context["disqualifications"]
    assert "Use evidence-backed housing outcomes" in context["application_guidance"]
    assert "Submit SAM registration" in context["requirements"]
    assert isinstance(context["recommended_outline"], list)
