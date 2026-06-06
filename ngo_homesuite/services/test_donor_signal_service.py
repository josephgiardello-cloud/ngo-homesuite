from __future__ import annotations

from datetime import datetime

import pytest

from ngo_homesuite.models.core import Donation, Donor, DonorEngagementScore, DonorJourneyAutomationEvent, Organization, Task, db
from ngo_homesuite.services.ai_insights_service import AIInsightsService
from ngo_homesuite.services.donor_signal_service import get_donor_signal, list_donor_signals
from ngo_homesuite.services.reporting_service import ReportingService


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


def _make_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def test_get_donor_signal_returns_canonical_payload(app):
    with app.app_context():
        org = _make_org("Signal Org", "signal-org")
        donor = Donor(organization_id=org.id, name="Signal Donor", email="signal@example.com")
        db.session.add(donor)
        db.session.flush()

        db.session.add_all(
            [
                Donation(
                    organization_id=org.id,
                    donor_id=donor.id,
                    donor_name="Signal Donor",
                    amount=50.0,
                    donation_date=datetime(2026, 1, 15, 12, 0, 0),
                    status="received",
                ),
                Task(
                    organization_id=org.id,
                    donor_id=donor.id,
                    title="Stewardship follow-up",
                    status="open",
                ),
                DonorJourneyAutomationEvent(
                    organization_id=org.id,
                    donor_id=donor.id,
                    trigger_name="retention_nudge",
                    action_type="email",
                    idempotency_key=f"signal-{org.id}-{donor.id}",
                    status="executed",
                ),
                DonorEngagementScore(
                    organization_id=org.id,
                    donor_id=donor.id,
                    score=73.0,
                    segment="loyal",
                    cultivation_priority="medium",
                    explanation="Healthy retention baseline",
                ),
            ]
        )
        db.session.commit()

        payload = get_donor_signal(org.id, donor=donor)

        assert payload["donor_id"] == donor.id
        assert payload["donor_name"] == "Signal Donor"
        assert payload["lifetime_total"] == 50.0
        assert payload["open_tasks"] == 1
        assert payload["journey_events"] == 1
        assert payload["engagement_score"] == 73.0
        assert payload["signal_version"] == "v1"
        assert payload["priority"] in {"low", "medium", "high"}


def test_list_donor_signals_orders_by_engagement_ascending(app):
    with app.app_context():
        org = _make_org("Signal Sort Org", "signal-sort-org")
        donor_low = Donor(organization_id=org.id, name="Low Score", email="low@example.com")
        donor_high = Donor(organization_id=org.id, name="High Score", email="high@example.com")
        db.session.add_all([donor_low, donor_high])
        db.session.flush()

        db.session.add_all(
            [
                DonorEngagementScore(organization_id=org.id, donor_id=donor_low.id, score=10.0),
                DonorEngagementScore(organization_id=org.id, donor_id=donor_high.id, score=90.0),
            ]
        )
        db.session.commit()

        rows = list_donor_signals(org.id, limit=2, ascending_engagement=True)

        assert len(rows) == 2
        assert rows[0]["donor_id"] == donor_low.id
        assert rows[1]["donor_id"] == donor_high.id


def test_donor_signal_required_keys_are_consistent_across_consumers(app):
    with app.app_context():
        org = _make_org("Signal Contract Org", "signal-contract-org")
        donor = Donor(organization_id=org.id, name="Contract Donor", email="contract@example.com")
        db.session.add(donor)
        db.session.flush()

        db.session.add(
            Donation(
                organization_id=org.id,
                donor_id=donor.id,
                donor_name="Contract Donor",
                amount=125.0,
                donation_date=datetime(2026, 2, 10, 12, 0, 0),
                status="received",
            )
        )
        db.session.commit()

        required = {
            "donor_id",
            "donor_name",
            "churn_risk",
            "lifetime_value_estimate",
            "priority",
            "signal_version",
        }

        canonical = get_donor_signal(org.id, donor=donor)
        legacy_snapshot = AIInsightsService._donor_ltv_snapshot(org.id, donor)
        report_signal = ReportingService().donor_profile_summary(org.id, donor.id)["donor_signal"]

        assert required.issubset(set(canonical.keys()))
        assert required.issubset(set(legacy_snapshot.keys()))
        assert required.issubset(set(report_signal.keys()))
