from __future__ import annotations

from datetime import datetime

import pytest

from ngo_homesuite.models.core import Beneficiary, Donation, Donor, Fund, Organization, Project, RecurringDonationPlan, db
from ngo_homesuite.services.reporting_service import ReportingService


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


def _make_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def test_organization_dashboard_summary(app):
    with app.app_context():
        org = _make_org("Reporting Org", "reporting-org")

        db.session.add_all(
            [
                Beneficiary(organization_id=org.id, first_name="A", last_name="One", status="active"),
                Beneficiary(organization_id=org.id, first_name="B", last_name="Two", status="inactive"),
                Project(organization_id=org.id, name="Project A", status="active", budget=150.0),
                Fund(organization_id=org.id, name="Fund A", is_active=True),
                Donor(organization_id=org.id, name="Donor A", email="a@example.com"),
            ]
        )
        db.session.flush()

        donor = Donor.query.filter_by(organization_id=org.id, name="Donor A").first()
        assert donor is not None
        db.session.add_all(
            [
                Donation(organization_id=org.id, donor_name="Donor A", donor_id=donor.id, amount=40.0, status="received"),
                Donation(organization_id=org.id, donor_name="Donor A", donor_id=donor.id, amount=60.0, status="received"),
            ]
        )
        db.session.commit()

        summary = ReportingService().organization_dashboard_summary(org.id, recent_donations_limit=2)

        assert summary["beneficiary_count"] == 1
        assert summary["project_count"] == 1
        assert summary["donor_count"] == 1
        assert summary["total_donations"] == 100.0
        assert summary["total_budget"] == 150.0
        assert summary["total_expenses"] == 0.0
        assert summary["net_cashflow"] == 100.0
        assert summary["total_funds"] == 1
        assert len(summary["recent_donations"]) == 2


def test_donor_profile_summary(app):
    with app.app_context():
        org = _make_org("Donor Summary Org", "donor-summary-org")
        donor = Donor(organization_id=org.id, name="Profile Donor", email="profile@example.com")
        db.session.add(donor)
        db.session.flush()

        db.session.add_all(
            [
                Donation(organization_id=org.id, donor_name="Profile Donor", donor_id=donor.id, amount=25.0, donation_date=datetime(2026, 1, 1, 12, 0, 0)),
                Donation(organization_id=org.id, donor_name="Profile Donor", donor_id=donor.id, amount=75.0, donation_date=datetime(2026, 2, 1, 12, 0, 0)),
                RecurringDonationPlan(organization_id=org.id, donor_id=donor.id, amount=10.0, next_charge_date=datetime(2026, 3, 1, 0, 0, 0), status="active"),
            ]
        )
        db.session.commit()

        summary = ReportingService().donor_profile_summary(org.id, donor.id, recent_limit=10)

        assert summary["donation_count"] == 2
        assert summary["donation_total"] == 100.0
        assert summary["first_gift_date"] == "2026-01-01"
        assert summary["last_gift_date"] == "2026-02-01"
        assert summary["active_recurring_plans"] == 1
        assert len(summary["recent_donations"]) == 2


def test_dashboard_summary_uses_cache_within_ttl(app, monkeypatch):
    with app.app_context():
        ReportingService._dashboard_cache.clear()
        previous_ttl = app.config.get("REPORTING_DASHBOARD_CACHE_TTL_SECONDS")
        app.config["REPORTING_DASHBOARD_CACHE_TTL_SECONDS"] = 120

        org = _make_org("Reporting Cache Org", "reporting-cache-org")
        db.session.add(Donor(organization_id=org.id, name="Cache Donor", email="cache@example.com"))
        db.session.add(Donation(organization_id=org.id, donor_name="Cache Donor", amount=33.0, status="received"))
        db.session.commit()

        service = ReportingService()
        first = service.organization_dashboard_summary(org.id, recent_donations_limit=2)
        assert first["total_donations"] == 33.0

        def _fail_db_access(*_args, **_kwargs):
            raise AssertionError("cache miss unexpectedly hit the database")

        monkeypatch.setattr(db.session, "scalar", _fail_db_access)
        monkeypatch.setattr(db.session, "execute", _fail_db_access)

        second = service.organization_dashboard_summary(org.id, recent_donations_limit=2)
        assert second["total_donations"] == first["total_donations"]

        if previous_ttl is None:
            app.config.pop("REPORTING_DASHBOARD_CACHE_TTL_SECONDS", None)
        else:
            app.config["REPORTING_DASHBOARD_CACHE_TTL_SECONDS"] = previous_ttl


def test_dashboard_summary_recent_limit_is_clamped(app):
    with app.app_context():
        ReportingService._dashboard_cache.clear()
        app.config["REPORTING_DASHBOARD_CACHE_TTL_SECONDS"] = 0

        org = _make_org("Reporting Clamp Org", "reporting-clamp-org")
        donor = Donor(organization_id=org.id, name="Clamp Donor", email="clamp@example.com")
        db.session.add(donor)
        db.session.flush()

        for idx in range(60):
            db.session.add(
                Donation(
                    organization_id=org.id,
                    donor_name="Clamp Donor",
                    donor_id=donor.id,
                    amount=float(idx + 1),
                    status="received",
                )
            )
        db.session.commit()

        summary = ReportingService().organization_dashboard_summary(org.id, recent_donations_limit=5000)
        assert len(summary["recent_donations"]) == 50