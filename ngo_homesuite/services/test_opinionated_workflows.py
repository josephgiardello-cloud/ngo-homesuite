from __future__ import annotations

from datetime import datetime, UTC

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donation, Organization, db
from ngo_homesuite.services.opinionated_workflows import run_donation_receipt_followup_workflow


def test_donation_followup_workflow_rejects_cross_tenant_donation():
    app = create_app(TestingConfig)

    with app.app_context():
        org_a = Organization(name="Workflow Org A", slug="workflow-org-a", is_active=True)
        org_b = Organization(name="Workflow Org B", slug="workflow-org-b", is_active=True)
        db.session.add_all([org_a, org_b])
        db.session.flush()

        donation_b = Donation(
            organization_id=org_b.id,
            donor_name="Cross Tenant Donor",
            amount=25.0,
            currency="USD",
            donation_date=datetime.now(UTC),
            status="received",
            purpose="Cross-tenant rejection test",
        )
        db.session.add(donation_b)
        db.session.commit()

        result = run_donation_receipt_followup_workflow(
            donation_id=donation_b.id,
            actor="workflow-test",
            organization_id=org_a.id,
        )

        assert result["ok"] is False
        assert "not found" in result["error"].lower()
