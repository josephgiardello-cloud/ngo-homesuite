from __future__ import annotations

from types import SimpleNamespace

import pytest

from ngo_homesuite.ai.minion_tools import MinionToolRegistry
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donation, DonationReceipt, Donor, Organization, db


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        MINION_ENABLED = True

    return create_app(_TestCfg)


@pytest.fixture()
def registry(app):
    with app.app_context():
        yield MinionToolRegistry()


def _runtime_ctx() -> dict[str, int | str]:
    return {
        "organization_id": 1,
        "actor": "test-minion",
    }


def test_donor_profile_insights_returns_predictive_summary(registry, app):
    with app.app_context():
        donor_id = 1

    payload = registry.execute("donor_profile_insights", {"donor_id": donor_id}, _runtime_ctx())

    assert payload["donor"]["id"] == donor_id
    assert isinstance(payload["insight_summary"], str)
    assert payload["metrics"]["predictions"]["giving_likelihood"] >= 0
    assert isinstance(payload["recommended_actions"], list)
    assert payload["recommended_actions"]


def test_rank_donors_for_outreach_returns_prioritized_list(registry):
    payload = registry.execute("rank_donors_for_outreach", {"limit": 2}, _runtime_ctx())

    assert payload["count"] >= 1
    assert len(payload["recommended"]) >= 1
    first = payload["recommended"][0]
    assert "priority_score" in first
    assert "suggested_next_action" in first


def test_summarize_donor_returns_risk_and_next_action(registry):
    payload = registry.execute("summarize_donor", {"donor_id": 1}, _runtime_ctx())

    assert payload["donor"]["id"] == 1
    assert isinstance(payload["summary"], str) and payload["summary"]
    assert "next_best_action" in payload
    assert isinstance(payload["risk_flags"], list)


def test_summarize_activity_timeline_returns_summary_and_actions(registry):
    payload = registry.execute(
        "summarize_activity_timeline",
        {"limit": 20, "entity_type": "donor"},
        _runtime_ctx(),
    )

    assert isinstance(payload["summary"], str) and payload["summary"]
    assert isinstance(payload["next_best_action"], str) and payload["next_best_action"]
    assert isinstance(payload["recommended_actions"], list)
    assert isinstance(payload["activity_totals"], dict)


def test_find_similar_donors_returns_ranked_matches(registry):
    payload = registry.execute("find_similar_donors", {"donor_id": 1, "limit": 3}, _runtime_ctx())

    assert payload["anchor_donor"]["id"] == 1
    assert payload["count"] >= 1
    assert len(payload["matches"]) >= 1
    assert "similarity_score" in payload["matches"][0]


def test_suggest_outreach_targets_returns_rationale(registry):
    payload = registry.execute("suggest_outreach_targets", {"limit": 2}, _runtime_ctx())

    assert payload["count"] >= 1
    assert len(payload["targets"]) >= 1
    assert "rationale" in payload["targets"][0]
    assert isinstance(payload["summary"], str) and payload["summary"]


def test_draft_personalized_appeal_uses_donor_context(registry):
    payload = registry.execute(
        "draft_personalized_appeal",
        {"donor_id": 1, "campaign_name": "Literacy Scholarship Drive", "ask_amount": 250},
        _runtime_ctx(),
    )

    assert payload["campaign_name"] == "Literacy Scholarship Drive"
    assert payload["ask_amount"] == 250.0
    assert "subject" in payload and payload["subject"]
    assert "body" in payload and "Fundraising Team" in payload["body"]


def test_execute_donation_followup_workflow_creates_receipt(registry, app):
    with app.app_context():
        donation = Donation.query.order_by(Donation.id.asc()).first()
        assert donation is not None
        donation_id = int(donation.id)

    payload = registry.execute(
        "execute_donation_followup_workflow",
        {"donation_id": donation_id},
        _runtime_ctx(),
    )

    assert payload["ok"] is True
    assert payload["workflow"] == "donation_receipt_followup"
    assert payload["donation_id"] == donation_id

    with app.app_context():
        receipt = DonationReceipt.query.filter_by(donation_id=donation_id).first()
        assert receipt is not None


def test_optional_relationship_counts_adds_org_filter_when_column_exists(registry, monkeypatch):
    class _FakeInspector:
        def get_table_names(self):
            return ["interactions", "pledges", "registrations"]

        def get_columns(self, table_name):
            return [{"name": "organization_id"}, {"name": "donor_id"}]

    captured: list[tuple[str, dict[str, int]]] = []

    class _ScalarResult:
        def __iter__(self):
            return iter([(11, 1)])

    def _fake_execute(statement, params):
        captured.append((str(statement), dict(params)))
        return _ScalarResult()

    monkeypatch.setattr("ngo_homesuite.ai.minion_tools.inspect", lambda _engine: _FakeInspector())
    monkeypatch.setattr("ngo_homesuite.ai.minion_tools.db.session.execute", _fake_execute)

    metrics = registry._optional_donor_relationship_counts(11, 42)

    assert metrics == {"interactions": 1, "pledges": 1, "events": 1}
    assert len(captured) == 3
    assert all("organization_id = :org_id" in sql for sql, _ in captured)
    assert all("donor_id IN" in sql for sql, _ in captured)
    assert all(params.get("org_id") == 42 for _, params in captured)
    assert all(params.get("donor_ids") == [11] for _, params in captured)


def test_generate_report_requires_organization_context(registry):
    payload = registry.execute(
        "generate_report",
        {"report_type": "donor_summary", "params": {}},
        {"actor": "test-minion"},
    )
    assert payload["error"] == "organization_id is required in runtime context"


def test_list_at_risk_donors_keeps_donor_hydration_tenant_scoped(registry, app, monkeypatch):
    with app.app_context():
        org_a = Organization.query.filter_by(is_active=True).first()
        assert org_a is not None
        org_a_id = int(org_a.id)

        org_b = Organization(name="Minion Org B", slug="minion-org-b", is_active=True)
        db.session.add(org_b)
        db.session.flush()
        org_b_id = int(org_b.id)

        donor_b = Donor(
            organization_id=org_b_id,
            name="Cross Tenant At Risk",
            email="cross@example.com",
            donor_type="individual",
        )
        db.session.add(donor_b)
        db.session.commit()
        donor_b_id = int(donor_b.id)

    def _fake_high_priority_lapsed(org_id, limit=20):
        return [SimpleNamespace(donor_id=donor_b_id, score=88.0, segment="lapsed", cultivation_priority="high")]

    monkeypatch.setattr("ngo_homesuite.services.engagement_scoring_service.high_priority_lapsed", _fake_high_priority_lapsed)

    payload = registry.execute("list_at_risk_donors", {"limit": 5}, {"organization_id": org_a_id, "actor": "test-minion"})

    assert payload["count"] == 1
    donor_row = payload["donors"][0]
    assert donor_row["donor_name"] == "Unknown"
    assert donor_row["email"] is None


def test_run_reconciliation_propagates_queued_reference_status(registry):
    payload = registry.execute(
        "run_reconciliation",
        {"bank_statement_ref": "bank_stmt_2026_05", "ledger_ref": "ledger_2026_05"},
        _runtime_ctx(),
    )

    assert payload["ok"] is True
    assert payload["status"] == "queued"
    assert payload["result"]["mode"] == "reference_only"


def test_run_reconciliation_reports_balanced_for_structured_result(registry, monkeypatch):
    def _fake_reconcile(*, bank_statement, ledger, actor):
        return {
            "mode": "structured",
            "status": "balanced",
            "matched_count": 2,
            "unmatched_bank_count": 0,
            "unmatched_ledger_count": 0,
        }

    monkeypatch.setattr(registry.bank_reconciliation_service, "reconcile", _fake_reconcile)

    payload = registry.execute(
        "run_reconciliation",
        {"bank_statement_ref": "b", "ledger_ref": "l"},
        _runtime_ctx(),
    )

    assert payload["ok"] is True
    assert payload["status"] == "balanced"
    assert payload["result"]["matched_count"] == 2

