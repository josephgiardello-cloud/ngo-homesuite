from __future__ import annotations

import pytest
from flask import g
from sqlalchemy import select

from ngo_homesuite.models.core import Donation, Grant, Organization, db


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


def test_rls_scopes_unfiltered_donation_queries_by_org(ctx):
    org_a = _mk_org("Tenant A", "tenant-a")
    org_b = _mk_org("Tenant B", "tenant-b")

    db.session.add(
        Donation(
            organization_id=org_a.id,
            donor_name="Donor A",
            amount=10.0,
            currency="USD",
            status="received",
            payment_method="manual",
        )
    )
    db.session.add(
        Donation(
            organization_id=org_b.id,
            donor_name="Donor B",
            amount=20.0,
            currency="USD",
            status="received",
            payment_method="manual",
        )
    )
    db.session.commit()

    g.organization_id = int(org_a.id)
    scoped = list(db.session.scalars(select(Donation).order_by(Donation.id.asc())))
    assert len(scoped) == 1
    assert int(scoped[0].organization_id) == int(org_a.id)

    unscoped = list(
        db.session.scalars(
            select(Donation).execution_options(skip_tenant_rls=True).order_by(Donation.id.asc())
        )
    )
    assert len(unscoped) >= 2


def test_rls_scopes_unfiltered_grant_queries_by_org(ctx):
    org_a = _mk_org("Grant Tenant A", "grant-tenant-a")
    org_b = _mk_org("Grant Tenant B", "grant-tenant-b")

    db.session.add(
        Grant(
            organization_id=org_a.id,
            funder_name="Funder A",
            title="Grant A",
            amount_requested=1000,
            amount_awarded=0,
            status="prospect",
        )
    )
    db.session.add(
        Grant(
            organization_id=org_b.id,
            funder_name="Funder B",
            title="Grant B",
            amount_requested=2000,
            amount_awarded=0,
            status="prospect",
        )
    )
    db.session.commit()

    g.organization_id = int(org_b.id)
    scoped = list(db.session.scalars(select(Grant).order_by(Grant.id.asc())))
    assert len(scoped) == 1
    assert int(scoped[0].organization_id) == int(org_b.id)


def test_rls_blocks_cross_tenant_writes(ctx):
    org_a = _mk_org("Write Tenant A", "write-tenant-a")
    org_b = _mk_org("Write Tenant B", "write-tenant-b")

    g.organization_id = int(org_a.id)
    db.session.add(
        Donation(
            organization_id=org_b.id,
            donor_name="Cross Tenant",
            amount=30.0,
            currency="USD",
            status="received",
            payment_method="manual",
        )
    )
    with pytest.raises(PermissionError, match="Tenant isolation violation"):
        db.session.commit()
    db.session.rollback()
