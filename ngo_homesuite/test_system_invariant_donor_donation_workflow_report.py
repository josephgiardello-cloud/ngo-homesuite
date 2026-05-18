from __future__ import annotations

import pytest

from ngo_homesuite.audit import DbEventStore
from ngo_homesuite.models.core import Donation, Donor, Organization, db
from ngo_homesuite.services.reporting_service import ReportingService
from ngo_homesuite.tenant import TenantContext


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


def test_donor_donation_workflow_report_invariant(app) -> None:
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        if org is None:
            org = Organization(name="Invariant Org", slug="invariant-org", is_active=True)
            db.session.add(org)
            db.session.commit()

        donor = Donor(
            organization_id=org.id,
            name="Invariant Donor",
            email="invariant.donor@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=123.45,
            currency="USD",
            status="received",
        )
        db.session.add(donation)
        db.session.commit()

        container = app.extensions["v2_container"]
        instance = container.create_workflow_instance(org_id=str(org.id), workflow_type="case_intake")
        tenant = TenantContext(org_id=str(org.id), user_id="invariant-user", role="case_worker")
        updated, replay = container.dispatch_workflow_event(
            instance_id=instance.instance_id,
            event_type="intake_submit",
            tenant=tenant,
            payload={"donor_id": donor.id, "donation_id": donation.id},
            idempotency_key=f"invariant-{instance.instance_id}-submit",
        )
        updated_replay, replay_flag = container.dispatch_workflow_event(
            instance_id=instance.instance_id,
            event_type="intake_submit",
            tenant=tenant,
            payload={"donor_id": donor.id, "donation_id": donation.id},
            idempotency_key=f"invariant-{instance.instance_id}-submit",
        )

        assert replay is False
        assert replay_flag is True
        assert updated.current_step == "verification"
        assert updated_replay.current_step == "verification"
        assert len(updated.history) == 1
        assert len(updated_replay.history) == 1

        audit_events = DbEventStore().list_events(org_id=str(org.id), aggregate_id=instance.instance_id)
        assert len(audit_events) >= 2

        dashboard = ReportingService().organization_dashboard_summary(org.id)
        assert float(dashboard.get("total_donations", 0.0)) >= 123.45
        assert int(dashboard.get("donor_count", 0)) >= 1
