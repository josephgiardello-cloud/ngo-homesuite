from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from ngo_homesuite.grants.models import Grant
from ngo_homesuite.grants.services import lifecycle as grant_service
from ngo_homesuite.grants.services.preaward import PreawardService
from ngo_homesuite.models.core import Donation, Donor, MembershipTier, Organization, ProgramCase, Task, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    shared_test_app.config["ROLES_REQUIRING_2FA"] = []
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login_admin(client):
    rv = client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin123!"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)


def test_v2_grant_advance_and_disbursement_contract(client, app):
    _login_admin(client)

    created = client.post(
        "/api/v2/grants",
        json={
            "title": "Contract Test Grant",
            "funder_name": "Route Contract Foundation",
            "amount_requested": 1200,
            "application_deadline": "2099-12-20",
        },
    )
    assert created.status_code == 201
    grant_id = created.get_json()["id"]

    missing_status = client.post(f"/api/v2/grants/{grant_id}/advance", json={})
    assert missing_status.status_code == 400

    advanced = client.post(
        f"/api/v2/grants/{grant_id}/advance",
        json={"new_status": "submitted"},
    )
    assert advanced.status_code == 200
    assert advanced.get_json()["status"] == "submitted"

    awarded = client.post(
        f"/api/v2/grants/{grant_id}/advance",
        json={"new_status": "awarded", "amount_awarded": 1000},
    )
    assert awarded.status_code == 200
    assert awarded.get_json()["status"] == "awarded"

    bad_date = client.post(
        f"/api/v2/grants/{grant_id}/disbursements",
        json={"amount": 500, "received_date": "15/05/2026"},
    )
    assert bad_date.status_code == 400

    disbursed = client.post(
        f"/api/v2/grants/{grant_id}/disbursements",
        json={"amount": 500, "received_date": "2026-05-15"},
    )
    assert disbursed.status_code == 201
    payload = disbursed.get_json()
    assert payload["amount"] == 500.0
    assert payload["received_date"] == "2026-05-15"

    close_without_approval = client.post(
        f"/api/v2/grants/{grant_id}/advance",
        json={"new_status": "closed"},
    )
    assert close_without_approval.status_code == 400
    assert "approval_request_id is required" in close_without_approval.get_json()["error"]

    with app.app_context():
        grant = db.session.scalar(select(Grant).where(Grant.id == grant_id))
        assert grant is not None
        grant_service.add_disbursement(grant_id, grant.organization_id, 500.0, date(2026, 5, 16))

        close_req = grant_service.create_approval_request(
            grant.organization_id,
            action_type="grant_closeout",
            resource_type="grant",
            resource_id=grant_id,
            requested_by_user_id=101,
            requested_by_role="staff",
        )
        grant_service.decide_approval_request(
            close_req.id,
            grant.organization_id,
            decided_by_user_id=202,
            decided_by_role="org_admin",
            decision="approved",
        )
        grant_service.decide_approval_request(
            close_req.id,
            grant.organization_id,
            decided_by_user_id=203,
            decided_by_role="controller",
            decision="approved",
        )

    closed = client.post(
        f"/api/v2/grants/{grant_id}/advance",
        json={"new_status": "closed", "approval_request_id": close_req.id},
    )
    assert closed.status_code == 200
    assert closed.get_json()["status"] == "closed"

    calendar = client.get("/api/v2/grants/calendar?within_days=30000")
    assert calendar.status_code == 200
    calendar_payload = calendar.get_json()
    assert isinstance(calendar_payload, list)
    assert any(int(item.get("grant_id", 0)) == int(grant_id) for item in calendar_payload)

    restricted = client.get("/api/v2/grants/restricted-funds")
    assert restricted.status_code == 200
    restricted_payload = restricted.get_json()
    assert isinstance(restricted_payload, dict)
    assert isinstance(restricted_payload.get("grants"), list)
    assert restricted_payload.get("total_awarded", 0) >= 1000
    assert restricted_payload.get("total_disbursed", 0) >= 500


def test_v2_grant_opportunity_search_and_compliance_guidance_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = db.session.scalar(select(User).where(User.username == "admin").limit(1))
        assert admin_user is not None
        opportunity = PreawardService.create_opportunity(
            int(admin_user.organization_id),
            funder_name="National Community Foundation",
            program_name="Youth Development",
            title="Youth Literacy Expansion 2026",
            deadline=date(2099, 12, 31),
            amount_min=50000,
            amount_max=150000,
            probability=0.72,
            status="qualified",
            notes="Applicants must include measurable outcomes and shall submit a quarterly compliance report.",
        )

    search_resp = client.get(
        "/api/v2/grants/opportunities/search"
        "?q=literacy+youth&applicant_profile=youth+education&requested_amount=100000&statuses=qualified"
    )
    assert search_resp.status_code == 200
    search_payload = search_resp.get_json()
    assert search_payload["count"] >= 1
    assert any(int(item["opportunity_id"]) == int(opportunity.id) for item in search_payload["results"])

    match = next(item for item in search_payload["results"] if int(item["opportunity_id"]) == int(opportunity.id))
    assert match["selectable"] is True
    assert float(match["applicability_score"]) > 0
    assert isinstance(match["match_reasons"], list)

    guidance_resp = client.post(
        f"/api/v2/grants/opportunities/{opportunity.id}/compliance-guidance",
        json={
            "proposal_text": "Our plan includes measurable outcomes and a reporting calendar to meet compliance expectations.",
        },
    )
    assert guidance_resp.status_code == 200
    guidance = guidance_resp.get_json()
    assert int(guidance["opportunity_id"]) == int(opportunity.id)
    assert isinstance(guidance["compliance_terms"], list)
    assert guidance["generated_by"] in {"deterministic", "ai"}
    assert "recommended_outline" in guidance

    ingest_resp = client.post(
        f"/api/v2/grants/opportunities/{opportunity.id}/guidelines/ingest",
        json={
            "guideline_text": "Applicants shall submit board-approved budgets and must provide quarterly narrative reports.",
            "source_name": "manual-guidelines.txt",
            "merge_into_notes": True,
        },
    )
    assert ingest_resp.status_code == 200
    ingest_payload = ingest_resp.get_json()
    assert int(ingest_payload["opportunity_id"]) == int(opportunity.id)
    assert ingest_payload["requirement_count"] >= 1
    assert ingest_payload["notes_updated"] is True

    draft_resp = client.post(
        f"/api/v2/grants/opportunities/{opportunity.id}/draft-assist",
        json={
            "organization_summary": "We are a youth-serving nonprofit with a decade of literacy programming.",
            "program_summary": "The program expands tutoring, family reading sessions, and school-based progress tracking.",
            "applicant_profile": "Youth education and family literacy outcomes",
            "amount_requested": 100000,
            "existing_draft": "We will measure outcomes and maintain a reporting calendar.",
        },
    )
    assert draft_resp.status_code == 200
    draft_payload = draft_resp.get_json()
    assert int(draft_payload["opportunity_id"]) == int(opportunity.id)
    assert draft_payload["generated_by"] in {"deterministic", "ai"}
    assert isinstance(draft_payload["draft_sections"], dict)
    assert "budget_and_compliance" in draft_payload["draft_sections"]
    assert isinstance(draft_payload["requirement_to_draft"], list)
    assert len(draft_payload["requirement_to_draft"]) >= 1
    assert isinstance(draft_payload["revision_suggestions"], list)
    assert "approval_readiness_summary" in draft_payload

    save_resp = client.post(
        f"/api/v2/grants/opportunities/{opportunity.id}/draft-assist/save",
        json={
            "organization_summary": "We are a youth-serving nonprofit with a decade of literacy programming.",
            "program_summary": "The program expands tutoring, family reading sessions, and school-based progress tracking.",
            "applicant_profile": "Youth education and family literacy outcomes",
            "amount_requested": 100000,
            "existing_draft": "We will measure outcomes and maintain a reporting calendar.",
            "document_ref": "saved-proposal.md",
        },
    )
    assert save_resp.status_code == 201
    save_payload = save_resp.get_json()
    assert int(save_payload["opportunity_id"]) == int(opportunity.id)
    assert int(save_payload["version_number"]) >= 1
    assert save_payload["document_ref"] == "saved-proposal.md"
    assert "Eligibility And Mission Fit" in str(save_payload["narrative_summary"])


def test_v2_grants_external_search_profiles_alerts_and_ai_context_contract(client, app, monkeypatch):
    _login_admin(client)

    with app.app_context():
        admin_user = db.session.scalar(select(User).where(User.username == "admin").limit(1))
        assert admin_user is not None
        opportunity = PreawardService.create_opportunity(
            int(admin_user.organization_id),
            funder_name="Grants.gov",
            program_name="Emergency Shelter",
            title="Emergency Shelter Federal Expansion",
            external_source="grants_gov",
            external_opportunity_id="GS-1001",
            external_url="https://www.grants.gov/search-results-detail/GS-1001",
            external_details_json={
                "eligibility": ["501(c)(3) nonprofits"],
                "disqualifications": ["For-profit applicants are ineligible"],
                "application_guidance": ["Include outcomes and staffing plan"],
                "requirements": ["Quarterly reporting"],
            },
            notes="Quarterly reporting and staffing plan required.",
        )

    def fake_search(*args, **kwargs):
        return [{
            "external_source": "grants_gov",
            "external_opportunity_id": "GS-2002",
            "title": "Federal Youth Services Grant",
            "funder_name": "Department of Youth Services",
            "program_name": "Youth Services",
            "external_url": "https://www.grants.gov/search-results-detail/GS-2002",
            "eligibility": ["Nonprofits"],
            "disqualifications": ["Individuals are not eligible"],
            "application_guidance": ["Map requirements to measurable outcomes"],
            "applicable_conditions": ["Semiannual reporting"],
            "requirements": ["Budget narrative"],
            "categories": ["Youth"],
            "applicability_score": 82.0,
            "match_reasons": ["Keyword overlap: youth"],
        }]

    def fake_sync(org_id, results):
        return []

    monkeypatch.setattr("ngo_homesuite.web.v2_routes._GRANTS_FACADE.search_grants_gov_opportunities", fake_search)
    monkeypatch.setattr("ngo_homesuite.web.v2_routes._GRANTS_FACADE.sync_grants_gov_results", fake_sync)

    external_resp = client.get(
        "/api/v2/grants/external/grants-gov/search?q=youth&applicant_profile=education&requested_amount=50000&sync=true"
    )
    assert external_resp.status_code == 200
    external_payload = external_resp.get_json()
    assert external_payload["count"] == 1
    assert external_payload["results"][0]["external_source"] == "grants_gov"
    assert "application_guidance" in external_payload["results"][0]

    ai_context_resp = client.get(f"/api/v2/grants/opportunities/{opportunity.id}/ai-context")
    assert ai_context_resp.status_code == 200
    ai_context = ai_context_resp.get_json()
    assert ai_context["external_source"] == "grants_gov"
    assert "501(c)(3) nonprofits" in ai_context["eligibility"]
    assert "Include outcomes and staffing plan" in ai_context["application_guidance"]

    profile_resp = client.post(
        "/api/v2/grants/search-profiles",
        json={
            "name": "Federal Youth Search",
            "query": "youth services",
            "applicant_profile": "nonprofit education",
            "requested_amount": 50000,
            "source": "grants_gov",
        },
    )
    assert profile_resp.status_code == 201
    profile_payload = profile_resp.get_json()
    assert profile_payload["name"] == "Federal Youth Search"

    list_resp = client.get("/api/v2/grants/search-profiles")
    assert list_resp.status_code == 200
    assert list_resp.get_json()["count"] >= 1

    def fake_run(profile_id, org_id):
        return {"profile_id": int(profile_id), "result_count": 1, "created_alerts": 1, "results": fake_search()}

    def fake_alerts(org_id, status=None, limit=50):
        return [{
            "id": 1,
            "profile_id": profile_payload["id"],
            "opportunity_id": None,
            "external_source": "grants_gov",
            "external_opportunity_id": "GS-2002",
            "title": "Federal Youth Services Grant",
            "status": "new",
            "matched_at": None,
            "details": {"application_guidance": ["Map requirements to measurable outcomes"]},
        }]

    monkeypatch.setattr("ngo_homesuite.web.v2_routes._GRANTS_FACADE.run_search_profile", fake_run)
    monkeypatch.setattr("ngo_homesuite.web.v2_routes._GRANTS_FACADE.list_search_alerts", fake_alerts)

    run_resp = client.post(f"/api/v2/grants/search-profiles/{profile_payload['id']}/run")
    assert run_resp.status_code == 200
    assert run_resp.get_json()["created_alerts"] == 1

    alerts_resp = client.get("/api/v2/grants/search-alerts")
    assert alerts_resp.status_code == 200
    alerts_payload = alerts_resp.get_json()
    assert alerts_payload["count"] == 1
    assert alerts_payload["results"][0]["external_source"] == "grants_gov"


def test_v2_p2p_detail_endpoints_require_auth(client):
    page_resp = client.get("/api/v2/p2p/pages/1", follow_redirects=False)
    progress_resp = client.get("/api/v2/p2p/pages/1/progress", follow_redirects=False)
    assert page_resp.status_code in (302, 401)
    assert progress_resp.status_code in (302, 401)


def test_v2_p2p_link_rejects_cross_tenant_donation(client, app):
    _login_admin(client)

    create_page_resp = client.post(
        "/api/v2/p2p/pages",
        json={"donor_id": 1, "title": "Tenant Safety Page"},
    )
    assert create_page_resp.status_code == 201
    page_id = create_page_resp.get_json()["id"]

    with app.app_context():
        org2 = Organization(name="Route Org Two", slug="route-org-two", is_active=True)
        db.session.add(org2)
        db.session.flush()

        donor2 = Donor(organization_id=org2.id, name="Org2 Donor", email="org2.donor@example.org")
        db.session.add(donor2)
        db.session.flush()

        donation2 = Donation(
            organization_id=org2.id,
            donor_id=donor2.id,
            donor_name=donor2.name,
            donor_email=donor2.email,
            amount=25.0,
            currency="USD",
            status="received",
            payment_method="bank_transfer",
        )
        db.session.add(donation2)
        db.session.commit()
        foreign_donation_id = donation2.id

    rv = client.post(
        f"/api/v2/p2p/pages/{page_id}/link-donation",
        json={"donation_id": foreign_donation_id},
    )
    assert rv.status_code == 400
    assert (rv.get_json() or {}).get("error") == "Invalid resource reference"


def test_v2_p2p_create_rejects_invalid_goal_input(client):
    _login_admin(client)

    rv = client.post(
        "/api/v2/p2p/pages",
        json={"donor_id": 1, "title": "Invalid Goal Page", "goal_amount": -5},
    )
    assert rv.status_code == 400


def test_v2_mutating_endpoints_reject_cross_tenant_references(client, app):
    _login_admin(client)

    with app.app_context():
        local_org = db.session.scalars(
            db.select(Organization).where(Organization.is_active == True).order_by(Organization.id.asc()).limit(1)
        ).first()
        assert local_org is not None
        local_org_id = int(local_org.id)

        local_donor = db.session.scalars(
            db.select(Donor).where(Donor.organization_id == local_org_id).limit(1)
        ).first()
        if local_donor is None:
            local_donor = Donor(
                organization_id=local_org_id,
                name="Tenant Matrix Local Donor",
                email="tenant-matrix-local@example.org",
            )
            db.session.add(local_donor)
            db.session.flush()

        foreign_org = Organization(name="Tenant Matrix Org", slug="tenant-matrix-org", is_active=True)
        db.session.add(foreign_org)
        db.session.flush()

        foreign_donor = Donor(
            organization_id=foreign_org.id,
            name="Tenant Matrix Foreign Donor",
            email="tenant-matrix-foreign@example.org",
        )
        db.session.add(foreign_donor)
        db.session.flush()

        foreign_tier = MembershipTier(
            organization_id=foreign_org.id,
            name="Foreign Tier",
            price=10,
            currency="USD",
            interval="annual",
        )
        db.session.add(foreign_tier)
        db.session.flush()

        foreign_task = Task(
            organization_id=foreign_org.id,
            title="Foreign Task",
            status="open",
            priority="medium",
            task_type="general",
        )
        db.session.add(foreign_task)
        db.session.flush()

        foreign_case = ProgramCase(
            organization_id=foreign_org.id,
            title="Foreign Case",
            case_type="service",
            status="open",
        )
        db.session.add(foreign_case)
        db.session.commit()

        foreign_donor_id = int(foreign_donor.id)
        foreign_tier_id = int(foreign_tier.id)
        foreign_task_id = int(foreign_task.id)
        foreign_case_id = int(foreign_case.id)
        local_donor_id = int(local_donor.id)

    create_page_with_foreign_donor = client.post(
        "/api/v2/p2p/pages",
        json={"donor_id": foreign_donor_id, "title": "Cross Tenant Page"},
    )
    assert create_page_with_foreign_donor.status_code == 400
    assert (create_page_with_foreign_donor.get_json() or {}).get("error") == "Invalid resource reference"

    enroll_with_foreign_tier = client.post(
        "/api/v2/membership/enroll",
        json={"donor_id": local_donor_id, "tier_id": foreign_tier_id},
    )
    assert enroll_with_foreign_tier.status_code == 404

    complete_foreign_task = client.post(f"/api/v2/tasks/{foreign_task_id}/complete", json={"notes": "x"})
    assert complete_foreign_task.status_code == 404

    advance_foreign_case = client.post(
        f"/api/v2/cases/{foreign_case_id}/status",
        json={"new_status": "closed"},
    )
    assert advance_foreign_case.status_code == 404


def test_v2_activity_feed_and_insights_contract(client):
    _login_admin(client)

    created = client.post(
        "/api/v2/tasks",
        json={"title": "Timeline contract task", "priority": "high"},
    )
    assert created.status_code == 201

    feed = client.get("/api/v2/activity/global?limit=20&entity_type=donor&q=donation")
    assert feed.status_code == 200
    feed_payload = feed.get_json()
    assert isinstance(feed_payload, list)

    unfiltered = client.get("/api/v2/activity/global?limit=20")
    assert unfiltered.status_code == 200
    unfiltered_payload = unfiltered.get_json()
    assert isinstance(unfiltered_payload, list)
    assert unfiltered_payload

    first = unfiltered_payload[0]
    assert isinstance(first.get("metadata"), dict)
    assert "activity_id" in first
    assert "entity_type" in first
    assert "entity_id" in first

    insights = client.get("/api/v2/activity/insights?limit=20&entity_type=donor")
    assert insights.status_code == 200
    payload = insights.get_json()
    assert isinstance(payload, dict)
    assert isinstance(payload.get("summary"), str)
    assert isinstance(payload.get("next_best_action"), str)
    assert isinstance(payload.get("recommended_actions"), list)


def test_v2_task_board_and_reminder_candidates_contract(client):
    _login_admin(client)

    created = client.post(
        "/api/v2/tasks",
        json={"title": "Board contract task", "priority": "high"},
    )
    assert created.status_code == 201

    board = client.get("/api/v2/tasks/board")
    assert board.status_code == 200
    payload = board.get_json()
    assert isinstance(payload, dict)
    assert isinstance(payload.get("summary"), dict)
    assert isinstance(payload.get("tasks"), list)
    assert isinstance(payload.get("reminder_candidates"), list)

    candidates = client.get("/api/v2/tasks/reminder-candidates?limit=15")
    assert candidates.status_code == 200
    assert isinstance(candidates.get_json(), list)
