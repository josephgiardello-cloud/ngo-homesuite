from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from ngo_homesuite.grants.models import Grant
from ngo_homesuite.grants.models import GrantBudgetLine, GrantBudgetTransaction
from ngo_homesuite.grants.services import lifecycle as grant_service
from ngo_homesuite.grants.services.preaward import PreawardService
from ngo_homesuite.models.core import (
    Beneficiary,
    Donation,
    Donor,
    Expense,
    FormSubmissionEvent,
    MembershipTier,
    Organization,
    ProgramCase,
    Project,
    RecurringDonationPlan,
    Task,
    TaskDependency,
    User,
    Volunteer,
    db,
)


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


def _ensure_user(app, username: str, email: str, role: str, password: str, org_id: int) -> None:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=email,
                role=role,
                is_active=True,
                organization_id=org_id,
            )
            user.set_password(password)
            db.session.add(user)
        else:
            user.email = email
            user.role = role
            user.is_active = True
            user.organization_id = org_id
            user.set_password(password)
        db.session.commit()


def _login_user(client, username: str, password: str) -> None:
    rv = client.post(
        "/auth/login",
        data={"username": username, "password": password},
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


def test_v2_grant_compliance_package_contract(client):
    _login_admin(client)

    created = client.post(
        "/api/v2/grants",
        json={
            "title": "Compliance Package Grant",
            "funder_name": "Contract Foundation",
            "amount_requested": 9000,
            "application_deadline": "2099-11-20",
            "report_due_date": "2099-12-20",
            "requirements": "Quarterly narrative and fiscal report",
        },
    )
    assert created.status_code == 201
    grant_id = int(created.get_json()["id"])

    awarded = client.post(
        f"/api/v2/grants/{grant_id}/advance",
        json={"new_status": "awarded", "amount_awarded": 8000},
    )
    assert awarded.status_code == 200

    disbursed = client.post(
        f"/api/v2/grants/{grant_id}/disbursements",
        json={"amount": 2500, "received_date": "2026-05-15"},
    )
    assert disbursed.status_code == 201

    package_rv = client.get(f"/api/v2/grants/{grant_id}/compliance-package")
    assert package_rv.status_code == 200
    payload = package_rv.get_json() or {}
    assert int((payload.get("grant") or {}).get("id") or 0) == grant_id
    assert isinstance(payload.get("checks"), list)
    assert isinstance(payload.get("budget_lines"), list)
    assert isinstance(payload.get("disbursements"), list)
    assert isinstance(payload.get("financials"), dict)
    assert "remaining_restricted_balance" in (payload.get("financials") or {})


def test_v2_grant_detail_blocks_cross_tenant_access_without_leaking_payload(client, app):
    with app.app_context():
        org_a = Organization.query.filter_by(slug="release-lane-org-a").first()
        if org_a is None:
            org_a = Organization(name="Release Lane Org A", slug="release-lane-org-a", is_active=True)
            db.session.add(org_a)
            db.session.flush()

        org_b = Organization.query.filter_by(slug="release-lane-org-b").first()
        if org_b is None:
            org_b = Organization(name="Release Lane Org B", slug="release-lane-org-b", is_active=True)
            db.session.add(org_b)
            db.session.flush()

        org_a_id = int(org_a.id)
        org_b_id = int(org_b.id)
        db.session.commit()

    _ensure_user(app, "rl_tenant_a", "rl_tenant_a@test.local", "staff", "ReleaseLane123!", org_a_id)
    _ensure_user(app, "rl_tenant_b", "rl_tenant_b@test.local", "staff", "ReleaseLane123!", org_b_id)

    _login_user(client, "rl_tenant_b", "ReleaseLane123!")
    created = client.post(
        "/api/v2/grants",
        json={
            "title": "Org B Private Grant",
            "funder_name": "Release Lane Foundation",
            "amount_requested": 7500,
        },
    )
    assert created.status_code == 201
    grant_id = created.get_json()["id"]

    client.post("/auth/logout")
    _login_user(client, "rl_tenant_a", "ReleaseLane123!")

    blocked = client.get(f"/api/v2/grants/{grant_id}")
    assert blocked.status_code == 404
    assert blocked.get_json() == {"error": "not found"}

    listed = client.get("/api/v2/grants")
    assert listed.status_code == 200
    assert all(item["id"] != grant_id for item in listed.get_json())


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


def test_v2_dedupe_workbench_surfaces_cross_entity_candidates_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = db.session.scalar(select(User).where(User.username == "admin").limit(1))
        assert admin_user is not None
        org_id = int(admin_user.organization_id)

        donor = Donor(
            organization_id=org_id,
            name="Alex Duplicate",
            email="alex.duplicate@example.org",
            phone="+1 (555) 010-9000",
            donor_type="individual",
        )
        beneficiary = Beneficiary(
            organization_id=org_id,
            first_name="Alex",
            last_name="Duplicate",
            email="alex.duplicate@example.org",
            phone="5550109000",
            status="active",
        )
        volunteer = Volunteer(
            organization_id=org_id,
            name="Alex Duplicate",
            email="alex.duplicate@example.org",
            phone="555-010-9000",
            status="active",
        )
        db.session.add_all([donor, beneficiary, volunteer])
        db.session.commit()

    rv = client.get("/api/v2/dedupe/workbench?entity_scope=all&limit=50")
    assert rv.status_code == 200
    payload = rv.get_json()
    assert isinstance(payload, dict)
    assert int(payload.get("count") or 0) >= 1

    candidates = payload.get("candidates") or []
    assert isinstance(candidates, list)
    match = next((item for item in candidates if str(item.get("reason") or "") == "matching_email"), None)
    assert match is not None
    records = match.get("records") or []
    entity_types = {str(item.get("entity_type") or "") for item in records}
    assert "donor" in entity_types
    assert "beneficiary" in entity_types
    assert "volunteer" in entity_types


def test_v2_dedupe_workbench_merge_contract_relinks_and_removes_duplicate(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = db.session.scalar(select(User).where(User.username == "admin").limit(1))
        assert admin_user is not None
        org_id = int(admin_user.organization_id)

        primary = Donor(
            organization_id=org_id,
            name="Merge Primary",
            email="merge.primary.contract@example.org",
            donor_type="individual",
        )
        duplicate = Donor(
            organization_id=org_id,
            name="Merge Duplicate",
            email="merge.duplicate.contract@example.org",
            donor_type="individual",
        )
        db.session.add_all([primary, duplicate])
        db.session.flush()

        donation = Donation(
            organization_id=org_id,
            donor_id=int(duplicate.id),
            donor_name=str(duplicate.name),
            donor_email=str(duplicate.email),
            donor_phone=str(duplicate.phone or ""),
            amount=125.0,
            currency="USD",
            donation_date=date.today(),
            status="received",
        )
        db.session.add(donation)
        db.session.commit()

        primary_id = int(primary.id)
        duplicate_id = int(duplicate.id)
        donation_id = int(donation.id)

    dry_run = client.post(
        "/api/v2/dedupe/workbench/merge",
        json={
            "primary_donor_id": primary_id,
            "duplicate_donor_id": duplicate_id,
            "dry_run": True,
        },
    )
    assert dry_run.status_code == 200
    dry_run_payload = dry_run.get_json()
    assert dry_run_payload["dry_run"] is True
    assert int(dry_run_payload["impact"]["duplicate_donation_count"]) == 1

    merge_rv = client.post(
        "/api/v2/dedupe/workbench/merge",
        json={
            "primary_donor_id": primary_id,
            "duplicate_donor_id": duplicate_id,
        },
    )
    assert merge_rv.status_code == 200
    merge_payload = merge_rv.get_json()
    assert merge_payload["merged"] is True
    assert int(merge_payload["primary_donor_id"]) == primary_id
    assert int(merge_payload["removed_donor_id"]) == duplicate_id
    assert int(merge_payload["relinked"]["donations"]) == 1

    with app.app_context():
        assert db.session.get(Donor, duplicate_id) is None
        updated = db.session.get(Donation, donation_id)
        assert updated is not None
        assert int(updated.donor_id) == primary_id


def test_v2_project_board_milestones_and_dependencies_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = db.session.scalar(select(User).where(User.username == "admin").limit(1))
        assert admin_user is not None
        org_id = int(admin_user.organization_id)

        project = Project(
            organization_id=org_id,
            name="Wave C Board Project",
            status="active",
            budget=0.0,
            spent=0.0,
            currency="USD",
        )
        db.session.add(project)
        db.session.flush()

        prereq = Task(
            organization_id=org_id,
            project_id=int(project.id),
            title="Define scope",
            status="open",
            priority="high",
            task_type="general",
        )
        dependent = Task(
            organization_id=org_id,
            project_id=int(project.id),
            title="Launch pilot",
            status="open",
            priority="medium",
            task_type="general",
        )
        db.session.add_all([prereq, dependent])
        db.session.commit()

        project_id = int(project.id)
        prereq_id = int(prereq.id)
        dependent_id = int(dependent.id)

    add_dep = client.post(
        f"/api/v2/tasks/{dependent_id}/dependencies",
        json={"depends_on_task_id": prereq_id, "dependency_type": "blocks"},
    )
    assert add_dep.status_code == 201
    dep_payload = add_dep.get_json()
    assert int(dep_payload["task_id"]) == dependent_id
    assert int(dep_payload["depends_on_task_id"]) == prereq_id

    board_before = client.get(f"/api/v2/projects/{project_id}/board")
    assert board_before.status_code == 200
    board_before_payload = board_before.get_json()
    assert int(board_before_payload["summary"]["total_tasks"]) == 2
    assert int(board_before_payload["summary"]["blocked_tasks"]) == 1
    dep_task_before = next(item for item in board_before_payload["tasks"] if int(item["id"]) == dependent_id)
    assert dep_task_before["blocked"] is True

    create_milestone = client.post(
        f"/api/v2/projects/{project_id}/milestones",
        json={
            "title": "Pilot ready",
            "description": "Must complete scope and comms before launch",
            "due_date": "2099-07-01T00:00:00",
            "status": "planned",
        },
    )
    assert create_milestone.status_code == 201
    milestone_payload = create_milestone.get_json()
    milestone_id = int(milestone_payload["id"])

    update_milestone = client.patch(
        f"/api/v2/projects/{project_id}/milestones/{milestone_id}",
        json={"status": "completed"},
    )
    assert update_milestone.status_code == 200
    assert update_milestone.get_json()["status"] == "completed"

    milestone_list = client.get(f"/api/v2/projects/{project_id}/milestones")
    assert milestone_list.status_code == 200
    milestone_list_payload = milestone_list.get_json()
    assert int(milestone_list_payload["count"]) >= 1
    assert any(int(item["id"]) == milestone_id and item["status"] == "completed" for item in milestone_list_payload["milestones"])

    with app.app_context():
        prereq_task = db.session.get(Task, prereq_id)
        assert prereq_task is not None
        prereq_task.status = "done"
        db.session.commit()

    board_after = client.get(f"/api/v2/projects/{project_id}/board")
    assert board_after.status_code == 200
    board_after_payload = board_after.get_json()
    assert int(board_after_payload["summary"]["blocked_tasks"]) == 0
    dep_task_after = next(item for item in board_after_payload["tasks"] if int(item["id"]) == dependent_id)
    assert dep_task_after["blocked"] is False

    remove_dep = client.delete(f"/api/v2/tasks/{dependent_id}/dependencies/{prereq_id}")
    assert remove_dep.status_code == 200
    assert remove_dep.get_json()["removed"] is True

    with app.app_context():
        remaining = db.session.scalar(
            select(TaskDependency).where(
                TaskDependency.organization_id == org_id,
                TaskDependency.task_id == dependent_id,
                TaskDependency.depends_on_task_id == prereq_id,
            ).limit(1)
        )
        assert remaining is None


def test_v2_collaboration_channels_messages_and_presence_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = db.session.scalar(select(User).where(User.username == "admin").limit(1))
        assert admin_user is not None
        org_id = int(admin_user.organization_id)

    _ensure_user(app, "collab_staff_peer", "collab_staff_peer@test.local", "staff", "CollabPeer123!", org_id)

    with app.app_context():
        peer = db.session.scalar(select(User).where(User.username == "collab_staff_peer").limit(1))
        assert peer is not None
        peer_id = int(peer.id)
        admin_id = int(admin_user.id)

    direct_create = client.post(
        "/api/v2/collab/channels",
        json={"channel_type": "direct", "participant_user_id": peer_id},
    )
    assert direct_create.status_code in {200, 201}
    direct_payload = direct_create.get_json()
    direct_channel = direct_payload["channel"]
    direct_channel_id = int(direct_channel["id"])

    direct_message = client.post(
        f"/api/v2/collab/channels/{direct_channel_id}/messages",
        json={"body": "Hello from Wave E contract"},
    )
    assert direct_message.status_code == 201
    message_payload = direct_message.get_json()
    assert int(message_payload["channel_id"]) == direct_channel_id
    assert int(message_payload["sender_user_id"]) == admin_id

    list_messages = client.get(f"/api/v2/collab/channels/{direct_channel_id}/messages")
    assert list_messages.status_code == 200
    list_messages_payload = list_messages.get_json()
    assert int(list_messages_payload["count"]) >= 1
    assert any(str(item.get("body") or "") == "Hello from Wave E contract" for item in list_messages_payload["messages"])

    team_create = client.post(
        "/api/v2/collab/channels",
        json={"channel_type": "team", "name": "Ops War Room", "member_user_ids": [peer_id]},
    )
    assert team_create.status_code == 201
    team_payload = team_create.get_json()
    assert team_payload["created"] is True
    team_channel_id = int(team_payload["channel"]["id"])

    channels_list = client.get("/api/v2/collab/channels")
    assert channels_list.status_code == 200
    channels_payload = channels_list.get_json()
    assert int(channels_payload["count"]) >= 1
    channel_ids = {int(item["id"]) for item in channels_payload["channels"]}
    assert direct_channel_id in channel_ids
    assert team_channel_id in channel_ids

    presence_upsert = client.post(
        "/api/v2/collab/presence",
        json={"status": "away", "status_message": "Reviewing grants"},
    )
    assert presence_upsert.status_code == 200
    presence_payload = presence_upsert.get_json()
    assert presence_payload["status"] == "away"
    assert int(presence_payload["user_id"]) == admin_id

    presence_list = client.get(f"/api/v2/collab/presence?user_ids={admin_id},{peer_id}")
    assert presence_list.status_code == 200
    presence_list_payload = presence_list.get_json()
    assert int(presence_list_payload["count"]) >= 1
    by_user = {int(item["user_id"]): item for item in presence_list_payload["items"]}
    assert by_user[admin_id]["status"] == "away"
    assert int(by_user[peer_id]["user_id"]) == peer_id


def test_v2_collaboration_message_endpoints_block_cross_tenant_access(client, app):
    with app.app_context():
        org_a = Organization.query.filter_by(slug="collab-route-org-a").first()
        if org_a is None:
            org_a = Organization(name="Collab Route Org A", slug="collab-route-org-a", is_active=True)
            db.session.add(org_a)
            db.session.flush()

        org_b = Organization.query.filter_by(slug="collab-route-org-b").first()
        if org_b is None:
            org_b = Organization(name="Collab Route Org B", slug="collab-route-org-b", is_active=True)
            db.session.add(org_b)
            db.session.flush()

        org_a_id = int(org_a.id)
        org_b_id = int(org_b.id)
        db.session.commit()

    _ensure_user(app, "collab_tenant_a", "collab_tenant_a@test.local", "staff", "CollabTenant123!", org_a_id)
    _ensure_user(app, "collab_tenant_b", "collab_tenant_b@test.local", "staff", "CollabTenant123!", org_b_id)

    _login_user(client, "collab_tenant_b", "CollabTenant123!")
    created = client.post(
        "/api/v2/collab/channels",
        json={"channel_type": "team", "name": "Tenant B Private", "member_user_ids": []},
    )
    assert created.status_code == 201
    channel_id = int(created.get_json()["channel"]["id"])

    message = client.post(
        f"/api/v2/collab/channels/{channel_id}/messages",
        json={"body": "Tenant B only"},
    )
    assert message.status_code == 201

    client.post("/auth/logout")
    _login_user(client, "collab_tenant_a", "CollabTenant123!")

    blocked_list = client.get(f"/api/v2/collab/channels/{channel_id}/messages")
    assert blocked_list.status_code == 404
    assert blocked_list.get_json() == {"error": "channel not found"}

    blocked_post = client.post(
        f"/api/v2/collab/channels/{channel_id}/messages",
        json={"body": "Should fail"},
    )
    assert blocked_post.status_code == 404
    assert blocked_post.get_json() == {"error": "channel not found"}

    listed = client.get("/api/v2/collab/channels")
    assert listed.status_code == 200
    assert all(int(item["id"]) != channel_id for item in listed.get_json()["channels"])


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


def test_v2_membership_members_list_filters_and_tenant_scope(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = db.session.scalars(
            db.select(User).where(User.username == "admin").limit(1)
        ).first()
        assert admin_user is not None
        local_org_id = int(admin_user.organization_id)

        local_tier = MembershipTier(
            organization_id=local_org_id,
            name="Contract Tier Local",
            price=50,
            currency="USD",
            interval="annual",
        )
        db.session.add(local_tier)
        db.session.flush()

        local_donor = Donor(
            organization_id=local_org_id,
            name="Membership Contract Person",
            email="membership-contract@example.org",
        )
        db.session.add(local_donor)
        db.session.flush()

        foreign_org = Organization(name="Membership Foreign Org", slug="membership-foreign-org", is_active=True)
        db.session.add(foreign_org)
        db.session.flush()
        foreign_tier = MembershipTier(
            organization_id=foreign_org.id,
            name="Contract Tier Foreign",
            price=99,
            currency="USD",
            interval="annual",
        )
        foreign_donor = Donor(
            organization_id=foreign_org.id,
            name="Membership Contract Person",
            email="foreign-membership-contract@example.org",
        )
        db.session.add_all([foreign_tier, foreign_donor])
        db.session.commit()

        local_tier_id = int(local_tier.id)
        local_donor_id = int(local_donor.id)

    enroll_rv = client.post(
        "/api/v2/membership/enroll",
        json={"donor_id": local_donor_id, "tier_id": local_tier_id},
    )
    assert enroll_rv.status_code == 201

    list_rv = client.get(
        "/api/v2/membership/members",
        query_string={
            "q": "Membership Contract Person",
            "status": "active",
            "page": 1,
            "page_size": 5,
        },
    )
    assert list_rv.status_code == 200
    payload = list_rv.get_json() or {}
    assert isinstance(payload.get("items"), list)
    assert isinstance(payload.get("pagination"), dict)
    assert int((payload.get("pagination") or {}).get("total") or 0) >= 1
    assert any(str(item.get("donor_email") or "") == "membership-contract@example.org" for item in payload.get("items") or [])
    assert all(str(item.get("donor_email") or "") != "foreign-membership-contract@example.org" for item in payload.get("items") or [])


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


def test_v2_donor_journey_and_soft_credit_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()
        assert admin_user is not None
        org_id = int(admin_user.organization_id)

        nonce = int(datetime.now(timezone.utc).timestamp() * 1000000)
        hard_credit_donor = Donor(
            organization_id=org_id,
            name=f"Journey Hard Credit {nonce}",
            email=f"journey-hard-{nonce}@example.org",
            donor_type="individual",
            status="active",
        )
        influencer_donor = Donor(
            organization_id=org_id,
            name=f"Journey Influencer {nonce}",
            email=f"journey-soft-{nonce}@example.org",
            donor_type="individual",
            status="active",
        )
        db.session.add_all([hard_credit_donor, influencer_donor])
        db.session.flush()

        donation = Donation(
            organization_id=org_id,
            donor_id=int(hard_credit_donor.id),
            donor_name=str(hard_credit_donor.name),
            donor_email=hard_credit_donor.email,
            amount=250.0,
            currency="USD",
            status="received",
            reference_number=f"SOFT-CREDIT-{nonce}",
        )
        db.session.add(donation)
        db.session.commit()

        donation_id = int(donation.id)
        influencer_id = int(influencer_donor.id)

    created = client.post(
        f"/api/v2/donations/{donation_id}/soft-credits",
        json={
            "donor_id": influencer_id,
            "role": "influencer",
            "credited_amount": 175.5,
            "credit_weight": 0.8,
            "rationale": "Introduced major donor to campaign",
        },
    )
    assert created.status_code == 201
    created_payload = created.get_json()
    assert created_payload["donation_id"] == donation_id
    assert created_payload["donor_id"] == influencer_id
    assert created_payload["role"] == "influencer"
    assert created_payload["credited_amount"] == 175.5

    listed = client.get(f"/api/v2/donations/{donation_id}/soft-credits")
    assert listed.status_code == 200
    listed_payload = listed.get_json()
    assert isinstance(listed_payload, list)
    assert any(
        int(item.get("donor_id", 0)) == influencer_id
        and int(item.get("donation_id", 0)) == donation_id
        for item in listed_payload
    )

    journey = client.get(f"/api/v2/donors/{influencer_id}/journey?limit=100")
    assert journey.status_code == 200
    journey_payload = journey.get_json()
    assert isinstance(journey_payload, dict)
    assert isinstance(journey_payload.get("donor"), dict)
    assert isinstance(journey_payload.get("summary"), dict)
    assert isinstance(journey_payload.get("timeline"), list)
    assert journey_payload["donor"]["id"] == influencer_id
    assert journey_payload["summary"].get("soft_credit_count", 0) >= 1
    assert journey_payload["summary"].get("soft_credit_total", 0) >= 175.5
    assert any(item.get("activity_type") == "soft_credit" for item in journey_payload["timeline"])


def test_v2_role_based_dashboard_intelligence_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()
        assert admin_user is not None
        org_id = int(admin_user.organization_id)

    _ensure_user(app, "intelligence_staff", "intelligence.staff@test.local", "staff", "IntelStaff123!", org_id)
    _ensure_user(app, "intelligence_viewer", "intelligence.viewer@test.local", "viewer", "IntelViewer123!", org_id)

    with app.app_context():
        staff_user = User.query.filter_by(username="intelligence_staff").first()
        assert staff_user is not None
        nonce = int(datetime.now(timezone.utc).timestamp() * 1000000)
        donor = Donor(
            organization_id=org_id,
            name=f"Intelligence Donor {nonce}",
            email=f"intelligence-donor-{nonce}@example.org",
            donor_type="individual",
            status="active",
        )
        db.session.add(donor)
        db.session.flush()

        donation = Donation(
            organization_id=org_id,
            donor_id=int(donor.id),
            donor_name=str(donor.name),
            donor_email=donor.email,
            amount=325.0,
            currency="USD",
            status="received",
            donation_date=datetime.now(timezone.utc).replace(tzinfo=None),
            reference_number=f"INTEL-{nonce}",
        )
        db.session.add(donation)

        task = Task(
            organization_id=org_id,
            assigned_to_id=int(staff_user.id),
            donor_id=int(donor.id),
            title="Follow up intelligence donor",
            task_type="follow_up",
            priority="high",
            status="open",
            due_date=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.session.add(task)
        db.session.commit()

    admin_payload_resp = client.get("/api/v2/intelligence/dashboard?period=30d")
    assert admin_payload_resp.status_code == 200
    admin_payload = admin_payload_resp.get_json()
    assert admin_payload["role"] == "admin"
    assert isinstance(admin_payload.get("scorecard"), list)
    assert isinstance(admin_payload.get("next_best_actions"), list)

    admin_preview_resp = client.get("/api/v2/intelligence/dashboard?period=30d&role=staff")
    assert admin_preview_resp.status_code == 200
    admin_preview_payload = admin_preview_resp.get_json()
    assert admin_preview_payload["role"] == "staff"
    assert "operational_queues" in admin_preview_payload

    bad_date_resp = client.get("/api/v2/intelligence/dashboard?start_date=06-02-2026")
    assert bad_date_resp.status_code == 400

    client.post("/auth/logout")
    _login_user(client, "intelligence_staff", "IntelStaff123!")

    staff_payload_resp = client.get("/api/v2/intelligence/dashboard?period=30d")
    assert staff_payload_resp.status_code == 200
    staff_payload = staff_payload_resp.get_json()
    assert staff_payload["role"] == "staff"
    staff_queue = staff_payload.get("operational_queues", {}).get("my_task_queue")
    assert isinstance(staff_queue, list)
    assert any(item.get("title") == "Follow up intelligence donor" for item in staff_queue)

    forbidden_preview = client.get("/api/v2/intelligence/dashboard?role=admin")
    assert forbidden_preview.status_code == 403
    assert forbidden_preview.get_json() == {"error": "Only admin users may preview alternate intelligence roles"}

    client.post("/auth/logout")
    _login_user(client, "intelligence_viewer", "IntelViewer123!")

    viewer_payload_resp = client.get("/api/v2/intelligence/dashboard?period=90d")
    assert viewer_payload_resp.status_code == 200
    viewer_payload = viewer_payload_resp.get_json()
    assert viewer_payload["role"] == "viewer"
    assert isinstance(viewer_payload.get("scorecard"), list)
    assert isinstance(viewer_payload.get("drilldowns"), dict)


def test_v2_financial_guardrails_intelligence_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()
        assert admin_user is not None
        org_id = int(admin_user.organization_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        project = Project(
            organization_id=org_id,
            name=f"Guardrail Project {int(now.timestamp() * 1000000)}",
            description="Project used for financial guardrail contract testing",
            budget=100.0,
            status="active",
        )
        db.session.add(project)
        db.session.flush()

        donor = Donor(
            organization_id=org_id,
            name=f"Guardrail Donor {int(now.timestamp() * 1000000)}",
            email=f"guardrail-donor-{int(now.timestamp() * 1000000)}@example.org",
            donor_type="individual",
            status="active",
        )
        db.session.add(donor)
        db.session.flush()

        db.session.add(
            Donation(
                organization_id=org_id,
                donor_id=int(donor.id),
                donor_name=donor.name,
                donor_email=donor.email,
                amount=50.0,
                currency="USD",
                status="received",
                donation_date=now - timedelta(days=2),
                reference_number=f"GRD-DON-{int(now.timestamp())}",
            )
        )

        db.session.add(
            Expense(
                organization_id=org_id,
                project_id=int(project.id),
                amount=1000000.0,
                currency="USD",
                payee="Guardrail Ops Vendor",
                description="Guardrail stress expense",
                paid_at=now - timedelta(days=1),
            )
        )

        grant = Grant(
            organization_id=org_id,
            title=f"Guardrail Grant {int(now.timestamp() * 1000000)}",
            funder_name="Guardrail Foundation",
            status="awarded",
            amount_awarded=250.0,
            award_date=(now - timedelta(days=30)).date(),
        )
        db.session.add(grant)
        db.session.flush()

        grant_line = GrantBudgetLine(
            organization_id=org_id,
            grant_id=int(grant.id),
            category="operations",
            line_name="Operations Line",
            allocated_amount=100.0,
            committed_amount=140.0,
            reconciled_amount=80.0,
            status="active",
        )
        db.session.add(grant_line)
        db.session.flush()

        db.session.add(
            GrantBudgetTransaction(
                organization_id=org_id,
                grant_id=int(grant.id),
                budget_line_id=int(grant_line.id),
                transaction_type="commit",
                amount=140.0,
                description="Unreconciled transaction for guardrails",
                created_by_user_id=int(admin_user.id),
                created_at=now - timedelta(days=45),
                reconciled_at=None,
                reconciled_by_user_id=None,
            )
        )
        db.session.commit()

        _ensure_user(app, "guardrail_staff", "guardrail.staff@test.local", "staff", "Guardrail123!", org_id)

    payload_resp = client.get("/api/v2/intelligence/financial-guardrails?period=30d")
    assert payload_resp.status_code == 200
    payload = payload_resp.get_json()
    assert payload["role"] == "admin"
    assert isinstance(payload.get("guardrails"), list)
    assert isinstance(payload.get("watchlists"), dict)
    assert isinstance(payload.get("next_best_actions"), list)
    assert "risk_score" in payload

    financial_posture = payload.get("financial_posture") or {}
    assert int(financial_posture.get("over_budget_projects", 0)) >= 1
    assert int(financial_posture.get("unreconciled_grant_transactions_over_30d", 0)) >= 1

    guardrails = payload.get("guardrails") or []
    guardrail_ids = {item.get("id") for item in guardrails}
    assert "period_net_cashflow" in guardrail_ids
    assert "project_budget_control" in guardrail_ids
    assert "grant_reconciliation_hygiene" in guardrail_ids
    budget_guardrail = next(item for item in guardrails if item.get("id") == "project_budget_control")
    assert budget_guardrail.get("status") in {"warning", "critical"}

    invalid_date = client.get("/api/v2/intelligence/financial-guardrails?start_date=06-02-2026")
    assert invalid_date.status_code == 400
    assert invalid_date.get_json() == {"error": "start_date must be ISO format YYYY-MM-DD"}

    staff_client = app.test_client()
    _login_user(staff_client, "guardrail_staff", "Guardrail123!")
    forbidden_preview = staff_client.get("/api/v2/intelligence/financial-guardrails?role=admin")
    assert forbidden_preview.status_code == 403
    assert forbidden_preview.get_json() == {"error": "Only admin users may preview alternate intelligence roles"}


def test_v2_donor_journey_automation_run_and_audit_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()
        assert admin_user is not None
        org_id = int(admin_user.organization_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # First-gift candidate: exactly one donation in window.
        first_gift_donor = Donor(
            organization_id=org_id,
            name=f"Automation First Gift {int(now.timestamp() * 1000000)}",
            email=f"automation-first-{int(now.timestamp() * 1000000)}@example.org",
            donor_type="individual",
            status="active",
        )
        db.session.add(first_gift_donor)
        db.session.flush()
        db.session.add(
            Donation(
                organization_id=org_id,
                donor_id=int(first_gift_donor.id),
                donor_name=first_gift_donor.name,
                donor_email=first_gift_donor.email,
                amount=120.0,
                currency="USD",
                status="received",
                donation_date=now - timedelta(days=1),
                reference_number=f"AUTO-FIRST-{int(now.timestamp())}",
            )
        )

        # Lapsing candidate: multiple gifts, none recently.
        lapsing_donor = Donor(
            organization_id=org_id,
            name=f"Automation Lapsing {int(now.timestamp() * 1000000) + 1}",
            email=f"automation-lapsing-{int(now.timestamp() * 1000000) + 1}@example.org",
            donor_type="individual",
            status="active",
        )
        db.session.add(lapsing_donor)
        db.session.flush()
        db.session.add_all(
            [
                Donation(
                    organization_id=org_id,
                    donor_id=int(lapsing_donor.id),
                    donor_name=lapsing_donor.name,
                    donor_email=lapsing_donor.email,
                    amount=80.0,
                    currency="USD",
                    status="received",
                    donation_date=now - timedelta(days=300),
                    reference_number=f"AUTO-LAPSE-A-{int(now.timestamp())}",
                ),
                Donation(
                    organization_id=org_id,
                    donor_id=int(lapsing_donor.id),
                    donor_name=lapsing_donor.name,
                    donor_email=lapsing_donor.email,
                    amount=60.0,
                    currency="USD",
                    status="received",
                    donation_date=now - timedelta(days=160),
                    reference_number=f"AUTO-LAPSE-B-{int(now.timestamp())}",
                ),
            ]
        )

        # Recurring failure candidate.
        recurring_donor = Donor(
            organization_id=org_id,
            name=f"Automation Recurring {int(now.timestamp() * 1000000) + 2}",
            email=f"automation-recurring-{int(now.timestamp() * 1000000) + 2}@example.org",
            donor_type="individual",
            status="active",
        )
        db.session.add(recurring_donor)
        db.session.flush()
        db.session.add(
            RecurringDonationPlan(
                organization_id=org_id,
                donor_id=int(recurring_donor.id),
                amount=35.0,
                currency="USD",
                frequency="monthly",
                next_charge_date=(now + timedelta(days=1)).date(),
                status="failed",
                fail_count=3,
                last_error="card_declined",
            )
        )

        db.session.commit()

    run_resp = client.post(
        "/api/v2/donor-journeys/automations/run",
        json={
            "first_gift_window_days": 14,
            "lapsing_days": 120,
            "recurring_fail_threshold": 2,
            "cooldown_days": 30,
        },
    )
    assert run_resp.status_code == 200
    run_payload = run_resp.get_json()
    assert isinstance(run_payload.get("summary"), dict)
    assert run_payload["summary"]["executed"] >= 3
    assert run_payload["summary"]["tasks_created"] >= 3
    assert isinstance(run_payload.get("actions"), list)

    events_resp = client.get("/api/v2/donor-journeys/automations/events?limit=200")
    assert events_resp.status_code == 200
    events_payload = events_resp.get_json()
    assert isinstance(events_payload, list)
    trigger_names = {item.get("trigger_name") for item in events_payload}
    assert "first_gift_followup" in trigger_names
    assert "lapsing_donor_reactivation" in trigger_names
    assert "recurring_failure_recovery" in trigger_names

    run_again_resp = client.post(
        "/api/v2/donor-journeys/automations/run",
        json={
            "first_gift_window_days": 14,
            "lapsing_days": 120,
            "recurring_fail_threshold": 2,
            "cooldown_days": 30,
        },
    )
    assert run_again_resp.status_code == 200
    run_again_payload = run_again_resp.get_json()
    assert run_again_payload["summary"]["executed"] == 0
    assert run_again_payload["summary"]["skipped"] >= 3

    filtered_resp = client.get(
        "/api/v2/donor-journeys/automations/events?trigger=recurring_failure_recovery&limit=50"
    )
    assert filtered_resp.status_code == 200
    filtered_payload = filtered_resp.get_json()
    assert isinstance(filtered_payload, list)
    assert filtered_payload
    assert all(item.get("trigger_name") == "recurring_failure_recovery" for item in filtered_payload)


def test_v2_integrated_form_ecosystem_ingest_dedupe_and_tenant_isolation_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()
        assert admin_user is not None
        org_id = int(admin_user.organization_id)
        nonce = int(datetime.now(timezone.utc).timestamp() * 1000000)

        existing_donor = Donor(
            organization_id=org_id,
            name=f"Forms Existing Donor {nonce}",
            email=f"forms-existing-{nonce}@example.org",
            donor_type="individual",
            status="active",
        )
        db.session.add(existing_donor)
        db.session.commit()
        existing_donor_id = int(existing_donor.id)
        existing_donor_email = str(existing_donor.email)

    ingest_payload = {
        "source": "typeform",
        "form_type": "donation",
        "form_name": "Spring Appeal Landing Form",
        "external_submission_id": f"tf-{int(datetime.now(timezone.utc).timestamp())}",
        "submitter_name": "Existing Forms Donor",
        "submitter_email": existing_donor_email,
        "submitter_phone": "+1-555-100-2000",
        "amount": 88.5,
        "currency": "USD",
        "purpose": "Spring appeal",
        "message": "Please apply this to the emergency response fund.",
        "follow_up_requested": True,
    }

    created = client.post("/api/v2/forms/submissions", json=ingest_payload)
    assert created.status_code == 201
    created_payload = created.get_json()
    assert created_payload["duplicate"] is False
    assert created_payload["source"] == "typeform"
    assert created_payload["form_type"] == "donation"
    assert created_payload["donor_id"] == existing_donor_id
    assert created_payload["donor_created"] is False
    assert created_payload["donation_id"] is not None
    assert created_payload["task_id"] is not None

    duplicate = client.post("/api/v2/forms/submissions", json=ingest_payload)
    assert duplicate.status_code == 200
    duplicate_payload = duplicate.get_json()
    assert duplicate_payload["duplicate"] is True
    assert duplicate_payload["submission_id"] == created_payload["submission_id"]
    assert duplicate_payload["donation_id"] == created_payload["donation_id"]
    assert duplicate_payload["task_id"] == created_payload["task_id"]

    listed = client.get("/api/v2/forms/submissions?source=typeform&form_type=donation&limit=20")
    assert listed.status_code == 200
    listed_payload = listed.get_json()
    assert isinstance(listed_payload, list)
    assert any(int(item.get("id", 0)) == int(created_payload["submission_id"]) for item in listed_payload)

    with app.app_context():
        event_count = db.session.scalar(
            select(db.func.count(FormSubmissionEvent.id)).where(
                FormSubmissionEvent.organization_id == org_id,
                FormSubmissionEvent.id == int(created_payload["submission_id"]),
            )
        )
        assert int(event_count or 0) == 1

        org_b = Organization.query.filter_by(slug="form-ecosystem-org-b").first()
        if org_b is None:
            org_b = Organization(name="Form Ecosystem Org B", slug="form-ecosystem-org-b", is_active=True)
            db.session.add(org_b)
            db.session.flush()
        org_b_id = int(org_b.id)
        db.session.commit()

    _ensure_user(app, "forms_staff_b", "forms.staff.b@test.local", "staff", "FormsStaffB123!", org_b_id)
    client.post("/auth/logout")
    _login_user(client, "forms_staff_b", "FormsStaffB123!")

    isolated_list = client.get("/api/v2/forms/submissions?limit=20")
    assert isolated_list.status_code == 200
    isolated_payload = isolated_list.get_json()
    assert isinstance(isolated_payload, list)
    assert all(int(item.get("id", 0)) != int(created_payload["submission_id"]) for item in isolated_payload)

    with app.app_context():
        app.config["FORM_ECOSYSTEM_INGEST_TOKEN"] = "forms-ingest-contract-token"

    public_bad_auth = client.post(
        "/api/v2/forms/submissions/public",
        json={
            "organization_id": org_id,
            "source": "webflow",
            "form_type": "contact",
            "external_submission_id": f"wf-bad-{int(datetime.now(timezone.utc).timestamp())}",
            "submitter_name": "Public Submitter",
            "submitter_email": f"public-bad-{int(datetime.now(timezone.utc).timestamp())}@example.org",
            "message": "Need information about your volunteer opportunities.",
        },
    )
    assert public_bad_auth.status_code == 401

    public_ok = client.post(
        "/api/v2/forms/submissions/public",
        headers={"X-Form-Ingest-Token": "forms-ingest-contract-token"},
        json={
            "organization_id": org_id,
            "source": "webflow",
            "form_type": "contact",
            "external_submission_id": f"wf-ok-{int(datetime.now(timezone.utc).timestamp())}",
            "submitter_name": "Public Submitter",
            "submitter_email": f"public-ok-{int(datetime.now(timezone.utc).timestamp())}@example.org",
            "message": "Need information about your volunteer opportunities.",
            "follow_up_requested": True,
        },
    )
    assert public_ok.status_code == 201
    public_ok_payload = public_ok.get_json()
    assert public_ok_payload["duplicate"] is False
    assert public_ok_payload["task_id"] is not None


def test_v2_task_board_and_reminder_candidates_contract(client, app):
    _login_admin(client)

    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()
        assert admin_user is not None
        org_id = int(admin_user.organization_id)

    _ensure_user(app, "task_assignee", "task.assignee@test.local", "staff", "TaskAssigneePass123!", org_id)

    with app.app_context():
        assignee = User.query.filter_by(username="task_assignee").first()
        assert assignee is not None
        assignee_id = assignee.id

    assignees = client.get("/api/v2/task-assignees")
    assert assignees.status_code == 200
    assignee_payload = assignees.get_json()
    assert isinstance(assignee_payload, list)
    assert any(user["id"] == assignee_id for user in assignee_payload)

    created = client.post(
        "/api/v2/tasks",
        json={
            "title": "Board contract task",
            "priority": "high",
            "assigned_to_id": assignee_id,
            "due_date": "2030-06-01",
            "reminder_channel": "email",
        },
    )
    assert created.status_code == 201
    task_id = created.get_json()["id"]

    updated = client.patch(
        f"/api/v2/tasks/{task_id}",
        json={
            "status": "in_progress",
            "assigned_to_id": admin_user.id,
            "due_date": "2030-06-15",
            "reminder_channel": "sms",
        },
    )
    assert updated.status_code == 200
    updated_payload = updated.get_json()
    assert updated_payload["status"] == "in_progress"
    assert updated_payload["assigned_to_id"] == admin_user.id
    assert updated_payload["due_date"].startswith("2030-06-15")
    assert updated_payload["reminder_channel"] == "sms"

    completed = client.post(f"/api/v2/tasks/{task_id}/complete", json={"notes": "Finished"})
    assert completed.status_code == 200
    assert completed.get_json()["status"] == "done"

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


def test_task_board_page_renders_management_controls(client):
    _login_admin(client)

    rv = client.get('/tasks/board')
    assert rv.status_code == 200

    body = rv.get_data(as_text=True)
    assert 'Quick Task Capture' in body
    assert 'Create Task' in body
    assert 'Save Changes' in body
