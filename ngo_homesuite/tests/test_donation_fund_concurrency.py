from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from ngo_homesuite.models.core import Donation, Fund, Organization, db
from ngo_homesuite.services.donation_service import DonationService
from ngo_homesuite.services.fund_service import FundService


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


def _make_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def _other_session():
    return sessionmaker(bind=db.engine, expire_on_commit=False)()


def test_donation_service_rejects_stale_update(app):
    with app.app_context():
        org = _make_org("Concurrency Donation Org", "concurrency-donation-org")
        donation = DonationService().create_donation(org.id, "Alex", 25.0)

        stale = DonationService().get_donation(donation.id, org.id)

        other = _other_session()
        try:
            row = other.query(Donation).filter_by(id=donation.id, organization_id=org.id).one()
            row.notes = "updated elsewhere"
            other.commit()
        finally:
            other.close()

        assert stale.version_id < 2

        with pytest.raises(RuntimeError, match="Concurrent update detected for donation"):
            DonationService().update_donation(donation.id, org.id, notes="updated locally")


def test_fund_service_rejects_stale_update(app):
    with app.app_context():
        org = _make_org("Concurrency Fund Org", "concurrency-fund-org")
        fund = FundService().create_fund(org.id, "General Fund")

        stale = FundService().get_fund(fund.id, org.id)

        other = _other_session()
        try:
            row = other.query(Fund).filter_by(id=fund.id, organization_id=org.id).one()
            row.description = "updated elsewhere"
            other.commit()
        finally:
            other.close()

        assert stale.version_id < 2

        with pytest.raises(RuntimeError, match="Concurrent update detected for fund"):
            FundService().update_fund(fund.id, org.id, description="updated locally")