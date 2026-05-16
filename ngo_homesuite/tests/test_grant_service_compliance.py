from __future__ import annotations

from datetime import date

import pytest

from ngo_homesuite.models.core import Organization, db
from ngo_homesuite.services import grant_service


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


def test_disbursement_blocked_before_awarded(ctx):
    org = _mk_org("Grant Compliance Org A", "grant-compliance-org-a")
    grant = grant_service.create_grant(
        organization_id=org.id,
        funder_name="Compliance Funder",
        title="Compliance Grant",
        amount_requested=1500,
    )

    with pytest.raises(ValueError, match="cannot disburse"):
        grant_service.add_disbursement(
            grant_id=grant.id,
            organization_id=org.id,
            amount=100,
            received_date=date(2026, 5, 16),
        )


def test_disbursement_cannot_exceed_award_amount(ctx):
    org = _mk_org("Grant Compliance Org B", "grant-compliance-org-b")
    grant = grant_service.create_grant(
        organization_id=org.id,
        funder_name="Compliance Funder B",
        title="Award Cap Grant",
        amount_requested=2000,
    )

    grant_service.advance_grant_status(grant.id, org.id, new_status="submitted")
    grant_service.advance_grant_status(
        grant.id,
        org.id,
        new_status="awarded",
        amount_awarded=1000,
    )

    grant_service.add_disbursement(
        grant_id=grant.id,
        organization_id=org.id,
        amount=800,
        received_date=date(2026, 5, 16),
    )

    with pytest.raises(ValueError, match="exceeds awarded"):
        grant_service.add_disbursement(
            grant_id=grant.id,
            organization_id=org.id,
            amount=250,
            received_date=date(2026, 5, 17),
        )


def test_close_blocked_until_restricted_balance_is_zero(ctx):
    org = _mk_org("Grant Compliance Org C", "grant-compliance-org-c")
    grant = grant_service.create_grant(
        organization_id=org.id,
        funder_name="Compliance Funder C",
        title="Closeout Grant",
        amount_requested=5000,
    )

    grant_service.advance_grant_status(grant.id, org.id, new_status="submitted")
    grant_service.advance_grant_status(
        grant.id,
        org.id,
        new_status="awarded",
        amount_awarded=1000,
    )

    with pytest.raises(grant_service.InvalidGrantTransition, match="outstanding restricted balance"):
        grant_service.advance_grant_status(grant.id, org.id, new_status="closed")

    grant_service.add_disbursement(
        grant_id=grant.id,
        organization_id=org.id,
        amount=1000,
        received_date=date(2026, 5, 18),
    )
    closed = grant_service.advance_grant_status(grant.id, org.id, new_status="closed")
    assert closed.status == "closed"


def test_award_transition_requires_awarded_amount(ctx):
    org = _mk_org("Grant Compliance Org D", "grant-compliance-org-d")
    grant = grant_service.create_grant(
        organization_id=org.id,
        funder_name="Compliance Funder D",
        title="Award Requirement Grant",
        amount_requested=1200,
    )

    grant_service.advance_grant_status(grant.id, org.id, new_status="submitted")
    with pytest.raises(ValueError, match="amount_awarded is required"):
        grant_service.advance_grant_status(grant.id, org.id, new_status="awarded")
