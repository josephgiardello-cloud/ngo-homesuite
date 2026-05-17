from __future__ import annotations

from ngo_homesuite.models.core import Organization, db
from ngo_homesuite.services import donation_service as donation_service_module
from ngo_homesuite.services.donation_service import DonationService


def _make_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def test_create_donation_increments_prometheus_counter(shared_test_app, monkeypatch):
    calls: list[float] = []

    def _capture(value: float = 1.0) -> None:
        calls.append(float(value))

    monkeypatch.setattr(donation_service_module, "inc_donations", _capture)

    with shared_test_app.app_context():
        org = _make_org("Donation Metrics Org", "donation-metrics-org")
        donation = DonationService().create_donation(org.id, "Metrics Donor", 42.0)
        assert donation.id is not None

    assert calls == [1.0]
