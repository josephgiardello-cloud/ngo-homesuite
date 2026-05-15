from __future__ import annotations

from datetime import datetime, timezone

import pytest
from werkzeug.exceptions import NotFound

from ngo_homesuite.models.core import Donation, Organization, Task, db
from ngo_homesuite.services import grant_service, task_service


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


def test_grant_service_scopes_reads_to_organization(ctx):
    org_a = _mk_org("Grant Org A", "grant-org-a")
    org_b = _mk_org("Grant Org B", "grant-org-b")

    grant_a = grant_service.create_grant(
        organization_id=org_a.id,
        funder_name="Funder A",
        title="Org A Grant",
        amount_requested=1000,
    )
    grant_b = grant_service.create_grant(
        organization_id=org_b.id,
        funder_name="Funder B",
        title="Org B Grant",
        amount_requested=2000,
    )

    assert grant_service.get_grant(grant_a.id, org_a.id) is not None
    assert grant_service.get_grant(grant_b.id, org_a.id) is None

    listed_a = grant_service.list_grants(org_a.id)
    listed_ids_a = {g.id for g in listed_a}
    assert grant_a.id in listed_ids_a
    assert grant_b.id not in listed_ids_a



def test_task_service_complete_task_rejects_cross_org_access(ctx):
    org_a = _mk_org("Task Org A", "task-org-a")
    org_b = _mk_org("Task Org B", "task-org-b")

    task = task_service.create_task(organization_id=org_a.id, title="Org A internal task")

    with pytest.raises(NotFound):
        task_service.complete_task(task.id, org_b.id, notes="attempt cross-tenant completion")



def test_task_service_major_donation_trigger_ignores_cross_org_donation(ctx):
    org_a = _mk_org("Donation Org A", "donation-org-a")
    org_b = _mk_org("Donation Org B", "donation-org-b")

    donation = Donation(
        organization_id=org_b.id,
        donor_name="Cross Org Donor",
        amount=1500.0,
        currency="USD",
        donation_date=datetime.now(timezone.utc),
        status="received",
        purpose="Cross org trigger guard",
    )
    db.session.add(donation)
    db.session.commit()

    created = task_service.auto_tasks_for_major_donation(
        donation_id=donation.id,
        organization_id=org_a.id,
        major_gift_threshold=500.0,
    )
    assert created == []

    leaked = Task.query.filter_by(organization_id=org_a.id, donation_id=donation.id).all()
    assert leaked == []
