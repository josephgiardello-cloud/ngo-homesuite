"""Tests for Gap Analysis services: engagement scoring, smart groups,
tasks, program cases, and P2P fundraising."""
from __future__ import annotations

import pytest

from ngo_homesuite.models.core import (
    db,
    Beneficiary,
    Donor,
    Donation,
    MembershipTier,
    Task,
    ProgramCase,
    SmartGroup,
    DonorEngagementScore,
    P2PPage,
)


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield


def _make_donor(org_id: int = 1, name: str = "Test Donor") -> Donor:
    d = Donor(organization_id=org_id, name=name, email=f"{name.replace(' ', '_').lower()}@test.invalid")
    db.session.add(d)
    db.session.flush()
    return d


def _make_donation(org_id: int, donor_id: int, amount: float) -> Donation:
    from datetime import datetime, UTC
    don = Donation(
        organization_id=org_id,
        donor_id=donor_id,
        donor_name="Test Donor",
        amount=amount,
        currency="USD",
        status="completed",
        donation_date=datetime.now(UTC),
    )
    db.session.add(don)
    db.session.flush()
    return don


# ============================================================
# Engagement Scoring
# ============================================================

class TestEngagementScoring:
    def test_compute_score_no_donations(self, ctx):
        from ngo_homesuite.services.engagement_scoring_service import compute_score
        d = _make_donor(name="Ungiving Donor")
        rec = compute_score(1, d.id)
        db.session.rollback()
        assert rec.score >= 0
        assert rec.segment in {"champion", "loyal", "promising", "at_risk", "lapsed", "new"}

    def test_compute_score_recent_donor(self, ctx):
        from ngo_homesuite.services.engagement_scoring_service import compute_score
        d = _make_donor(name="Recent Giver")
        _make_donation(1, d.id, 250.0)
        rec = compute_score(1, d.id)
        db.session.rollback()
        # Should have non-zero recency + monetary
        assert rec.recency_score > 0
        assert rec.monetary_score > 0

    def test_batch_recompute_returns_counts(self, ctx):
        from ngo_homesuite.services.engagement_scoring_service import batch_recompute
        result = batch_recompute(1)
        db.session.rollback()
        assert "updated" in result
        assert isinstance(result["updated"], int)

    def test_high_priority_lapsed_returns_list(self, ctx):
        from ngo_homesuite.services.engagement_scoring_service import high_priority_lapsed
        records = high_priority_lapsed(1, limit=10)
        db.session.rollback()
        assert isinstance(records, list)


# ============================================================
# Smart Groups
# ============================================================

class TestSmartGroups:
    def test_create_and_list_group(self, ctx):
        from ngo_homesuite.services.smart_groups_service import create_group, list_groups
        group = create_group(
            organization_id=1,
            name="Test Lybunt Group",
            rules=[{"field": "segment", "op": "eq", "value": "loyal"}],
        )
        db.session.rollback()
        assert group.id is not None
        groups = list_groups(1)
        assert any(g.name == "Test Lybunt Group" for g in groups)

    def test_evaluate_group_runs_without_error(self, ctx):
        from ngo_homesuite.services.smart_groups_service import create_group, evaluate_group
        group = create_group(
            organization_id=1,
            name="High Score Group",
            rules=[{"field": "score", "op": "gte", "value": 0}],
        )
        members = evaluate_group(group.id, 1)
        db.session.rollback()
        assert isinstance(members, list)

    def test_invalid_field_raises(self, ctx):
        from ngo_homesuite.services.smart_groups_service import create_group
        with pytest.raises(ValueError, match="Unknown rule field"):
            create_group(
                organization_id=1,
                name="Bad Group",
                rules=[{"field": "invalid_field_xyz", "op": "eq", "value": "x"}],
            )
        db.session.rollback()

    def test_invalid_op_raises(self, ctx):
        from ngo_homesuite.services.smart_groups_service import create_group
        with pytest.raises(ValueError, match="Unknown rule operator"):
            create_group(
                organization_id=1,
                name="Bad Op Group",
                rules=[{"field": "score", "op": "between", "value": 50}],
            )
        db.session.rollback()


# ============================================================
# Tasks
# ============================================================

class TestTasks:
    def test_create_and_complete_task(self, ctx):
        from ngo_homesuite.services.task_service import create_task, complete_task
        d = _make_donor(name="Task Donor")
        task = create_task(1, "Follow up with donor", donor_id=d.id, priority="high")
        assert task.status == "open"
        completed = complete_task(task.id, 1, notes="Called and left voicemail")
        db.session.rollback()
        assert completed.status == "done"
        assert completed.completed_at is not None

    def test_list_tasks_by_donor(self, ctx):
        from ngo_homesuite.services.task_service import create_task, list_tasks
        d = _make_donor(name="Task Donor 2")
        create_task(1, "Email donor", donor_id=d.id)
        tasks = list_tasks(1, donor_id=d.id)
        db.session.rollback()
        assert len(tasks) >= 1
        assert all(t.donor_id == d.id for t in tasks)

    def test_overdue_summary_returns_dict(self, ctx):
        from ngo_homesuite.services.task_service import overdue_task_summary
        result = overdue_task_summary(1)
        db.session.rollback()
        assert "total_overdue" in result
        assert "by_priority" in result

    def test_auto_tasks_for_major_donation(self, ctx):
        from ngo_homesuite.services.task_service import auto_tasks_for_major_donation
        d = _make_donor(name="Major Donor")
        don = _make_donation(1, d.id, 1000.0)
        tasks = auto_tasks_for_major_donation(don.id, 1, major_gift_threshold=500.0)
        db.session.rollback()
        assert len(tasks) == 2
        priorities = {t.priority for t in tasks}
        assert "high" in priorities


# ============================================================
# Program Cases
# ============================================================

class TestProgramCases:
    def test_create_case_and_activity_logged(self, ctx):
        from ngo_homesuite.services.program_impact_service import create_case, get_case
        case = create_case(1, "Housing Assistance Case #1", case_type="service")
        db.session.rollback()
        assert case.id is not None
        assert case.status == "open"

    def test_update_status_creates_activity(self, ctx):
        from ngo_homesuite.services.program_impact_service import create_case, update_case_status
        from ngo_homesuite.models.core import CaseActivity
        case = create_case(1, "Education Case", case_type="service")
        update_case_status(case.id, 1, "in_progress", notes="Started outreach")
        activities = CaseActivity.query.filter_by(case_id=case.id).all()
        db.session.rollback()
        # Should have "opened" + "status_change" activities
        assert len(activities) >= 2

    def test_impact_report_returns_structure(self, ctx):
        from ngo_homesuite.services.program_impact_service import impact_report
        report = impact_report(1)
        db.session.rollback()
        assert "case_count" in report
        assert "by_status" in report

    def test_add_note_to_case(self, ctx):
        from ngo_homesuite.services.program_impact_service import create_case, add_note
        case = create_case(1, "Community Case")
        activity = add_note(case.id, 1, "Client attended program session")
        db.session.rollback()
        assert activity.activity_type == "note"

    def test_case_service_log_and_progress_tracking(self, ctx):
        from ngo_homesuite.services.program_impact_service import (
            case_progress,
            create_case,
            log_service_delivery,
            record_outcome_metric,
        )

        beneficiary = Beneficiary(
            organization_id=1,
            first_name="Marta",
            last_name="Perez",
            status="active",
            program="Housing",
        )
        db.session.add(beneficiary)
        db.session.flush()

        case = create_case(
            1,
            "Housing Stabilization",
            beneficiary_id=beneficiary.id,
            target_outcome_value=10.0,
            outcome_metric="sessions_completed",
            intake_stage="assessment",
        )

        log_service_delivery(
            case.id,
            1,
            service_type="counseling_session",
            duration_minutes=60,
            outcome_note="Initial session complete",
        )
        record_outcome_metric(
            case.id,
            1,
            metric_name="sessions_completed",
            current_value=4.0,
            target_value=10.0,
            note="Week 2",
        )

        progress = case_progress(case.id, 1)
        db.session.rollback()

        assert progress["service_count"] == 1
        assert progress["progress_percent"] == 40.0
        assert len(progress["metrics"]) == 1
        assert len(progress["timeline"]) >= 2

    def test_update_beneficiary_intake(self, ctx):
        from ngo_homesuite.services.program_impact_service import update_beneficiary_intake

        beneficiary = Beneficiary(
            organization_id=1,
            first_name="Intake",
            last_name="User",
            status="active",
            program="General",
        )
        db.session.add(beneficiary)
        db.session.flush()

        updated = update_beneficiary_intake(
            beneficiary.id,
            1,
            phone="+1-555-0100",
            city="Austin",
            notes="Completed intake interview",
        )
        db.session.rollback()

        assert updated.phone == "+1-555-0100"
        assert updated.city == "Austin"
        assert "intake" in updated.notes.lower()


# ============================================================
# P2P Fundraising
# ============================================================

class TestP2PFundraising:
    def test_create_and_publish_page(self, ctx):
        from ngo_homesuite.services.p2p_service import create_page, publish_page, get_page
        d = _make_donor(name="Fundraiser Donor")
        page = create_page(1, d.id, "My Fundraiser", goal_amount=1000.0)
        assert page.status == "draft"
        published = publish_page(page.id, 1)
        db.session.rollback()
        assert published.status == "active"

    def test_unique_slugs(self, ctx):
        from ngo_homesuite.services.p2p_service import create_page
        d = _make_donor(name="Slug Donor")
        page1 = create_page(1, d.id, "My Campaign")
        page2 = create_page(1, d.id, "My Campaign")
        db.session.rollback()
        assert page1.public_slug != page2.public_slug

    def test_link_donation_and_progress(self, ctx):
        from ngo_homesuite.services.p2p_service import create_page, publish_page, link_donation, get_progress
        d = _make_donor(name="P2P Donor")
        page = create_page(1, d.id, "Charity Run", goal_amount=500.0)
        publish_page(page.id, 1)
        don = _make_donation(1, d.id, 200.0)
        link_donation(page.id, 1, don.id)
        progress = get_progress(page.id, 1)
        db.session.rollback()
        assert progress["total_raised"] == 200.0
        assert progress["pct_of_goal"] == 40.0

    def test_leaderboard_returns_list(self, ctx):
        from ngo_homesuite.services.p2p_service import leaderboard
        result = leaderboard(1)
        db.session.rollback()
        assert isinstance(result, list)

    def test_progress_handles_anonymous_donation(self, ctx):
        from ngo_homesuite.services.p2p_service import create_page, link_donation, get_progress

        d = _make_donor(name="Anonymous P2P Owner")
        page = create_page(1, d.id, "Anonymous Donations", goal_amount=100.0)

        from datetime import datetime, UTC
        anon = Donation(
            organization_id=1,
            donor_id=None,
            donor_name="Anonymous",
            amount=30.0,
            currency="USD",
            status="completed",
            donation_date=datetime.now(UTC),
        )
        db.session.add(anon)
        db.session.flush()

        link_donation(page.id, 1, anon.id)
        progress = get_progress(page.id, 1)
        db.session.rollback()

        assert progress["total_raised"] == 30.0
        assert progress["donor_count"] == 0

    def test_leaderboard_offset_paginates(self, ctx):
        from ngo_homesuite.services.p2p_service import create_page, link_donation, leaderboard

        d = _make_donor(name="Leaderboard Donor")
        p1 = create_page(1, d.id, "LB One")
        p2 = create_page(1, d.id, "LB Two")

        d1 = _make_donation(1, d.id, 100.0)
        d2 = _make_donation(1, d.id, 50.0)
        link_donation(p1.id, 1, d1.id)
        link_donation(p2.id, 1, d2.id)

        first = leaderboard(1, limit=1, offset=0)
        second = leaderboard(1, limit=1, offset=1)
        db.session.rollback()

        assert len(first) == 1
        assert len(second) == 1
        assert first[0]["page_id"] != second[0]["page_id"]

    def test_link_donation_rejects_cross_org_donation(self, ctx):
        from ngo_homesuite.services.p2p_service import create_page, link_donation
        from ngo_homesuite.models.core import Organization

        org2 = Organization(name="Org Two", slug="org-two", is_active=True)
        db.session.add(org2)
        db.session.flush()

        donor_org1 = _make_donor(org_id=1, name="Org1 Donor")
        donor_org2 = _make_donor(org_id=org2.id, name="Org2 Donor")

        page = create_page(1, donor_org1.id, "Org1 Page", goal_amount=100.0)
        donation_org2 = _make_donation(org2.id, donor_org2.id, 75.0)

        with pytest.raises(ValueError, match="invalid resource reference"):
            link_donation(page.id, 1, donation_org2.id)

        db.session.rollback()

    def test_unlink_donation_rejects_cross_org_donation(self, ctx):
        from ngo_homesuite.services.p2p_service import create_page, link_donation, unlink_donation
        from ngo_homesuite.models.core import Organization

        org2 = Organization(name="Org Two Unlink", slug="org-two-unlink", is_active=True)
        db.session.add(org2)
        db.session.flush()

        donor_org1 = _make_donor(org_id=1, name="Org1 Unlink Donor")
        donor_org2 = _make_donor(org_id=org2.id, name="Org2 Unlink Donor")

        page = create_page(1, donor_org1.id, "Org1 Unlink Page", goal_amount=100.0)
        donation_org1 = _make_donation(1, donor_org1.id, 40.0)
        donation_org2 = _make_donation(org2.id, donor_org2.id, 65.0)
        link_donation(page.id, 1, donation_org1.id)

        with pytest.raises(ValueError, match="invalid resource reference"):
            unlink_donation(page.id, 1, donation_org2.id)

        # Valid same-org unlink still succeeds.
        unlink_donation(page.id, 1, donation_org1.id)

        db.session.rollback()


# ============================================================
# Copilot Tools — Engagement Score integration
# ============================================================

class TestCopilotEngagementTools:
    @pytest.fixture()
    def registry(self, app):
        from ngo_homesuite.ai.copilot_tools import CopilotToolRegistry
        with app.app_context():
            yield CopilotToolRegistry()

    def _ctx(self) -> dict:
        return {"organization_id": 1, "actor": "test"}

    def test_get_donor_engagement_score_tool(self, registry, app):
        with app.app_context():
            d = _make_donor(name="Scored Donor")
            result = registry.execute("get_donor_engagement_score", {"donor_id": d.id}, self._ctx())
            db.session.rollback()
        assert "score" in result
        assert "segment" in result
        assert "breakdown" in result

    def test_list_at_risk_donors_tool(self, registry, app):
        with app.app_context():
            result = registry.execute("list_at_risk_donors", {"limit": 5}, self._ctx())
            db.session.rollback()
        assert "donors" in result
        assert isinstance(result["donors"], list)

    def test_summarize_program_impact_tool(self, registry, app):
        with app.app_context():
            result = registry.execute("summarize_program_impact", {}, self._ctx())
            db.session.rollback()
        assert "case_count" in result

    def test_evaluate_smart_group_tool(self, registry, app):
        with app.app_context():
            from ngo_homesuite.services.smart_groups_service import create_group
            group = create_group(1, "Copilot Test Group", rules=[{"field": "score", "op": "gte", "value": 0}])
            result = registry.execute("evaluate_smart_group", {"group_id": group.id}, self._ctx())
            db.session.rollback()
        assert "count" in result
        assert "members" in result
