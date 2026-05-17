from __future__ import annotations

from ngo_homesuite.models.core import Campaign, Donation, Donor, Organization, db
from ngo_homesuite.services.campaign_service import campaign_stats, create_campaign, get_campaign
from ngo_homesuite.services.donation_service import DonationService
from ngo_homesuite.services import p2p_service


def _make_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def test_campaign_raised_amount_recalculates_on_donation_create(shared_test_app):
    with shared_test_app.app_context():
        org = _make_org("Campaign Totals Org", "campaign-totals-org")
        campaign = create_campaign(organization_id=org.id, name="Spring Drive", status="active")

        service = DonationService()
        service.create_donation(org.id, "Donor A", 25.0, status="received", campaign_id=campaign.id)
        service.create_donation(org.id, "Donor B", 30.0, status="processed", campaign_id=campaign.id)
        service.create_donation(org.id, "Donor C", 99.0, status="pending", campaign_id=campaign.id)

        refreshed = get_campaign(campaign.id, org.id)
        assert refreshed is not None
        assert float(refreshed.raised_amount) == 55.0

        stats = campaign_stats(campaign.id, org.id)
        assert float(stats["received_amount"]) == 55.0
        assert float(stats["pending_amount"]) == 99.0
        assert float(stats["pledged_amount"]) == 154.0


def test_campaign_raised_amount_updates_on_donation_amount_change(shared_test_app):
    with shared_test_app.app_context():
        org = _make_org("Campaign Update Org", "campaign-update-org")
        campaign = create_campaign(organization_id=org.id, name="Update Drive", status="active")

        service = DonationService()
        donation = service.create_donation(
            org.id,
            "Donor Update",
            25.0,
            status="received",
            campaign_id=campaign.id,
        )

        refreshed = get_campaign(campaign.id, org.id)
        assert refreshed is not None
        assert float(refreshed.raised_amount) == 25.0

        donation.amount = 40.0
        db.session.commit()

        refreshed = get_campaign(campaign.id, org.id)
        assert refreshed is not None
        assert float(refreshed.raised_amount) == 40.0

        stats = campaign_stats(campaign.id, org.id)
        assert float(stats["received_amount"]) == 40.0
        assert float(stats["pledged_amount"]) == 40.0


def test_campaign_raised_amount_decreases_on_donation_delete(shared_test_app):
    with shared_test_app.app_context():
        org = _make_org("Campaign Delete Org", "campaign-delete-org")
        campaign = create_campaign(organization_id=org.id, name="Delete Drive", status="active")

        service = DonationService()
        first = service.create_donation(org.id, "Donor One", 20.0, status="received", campaign_id=campaign.id)
        second = service.create_donation(org.id, "Donor Two", 35.0, status="received", campaign_id=campaign.id)

        refreshed = get_campaign(campaign.id, org.id)
        assert refreshed is not None
        assert float(refreshed.raised_amount) == 55.0

        db.session.delete(second)
        db.session.commit()

        refreshed = get_campaign(campaign.id, org.id)
        assert refreshed is not None
        assert float(refreshed.raised_amount) == 20.0

        stats = campaign_stats(campaign.id, org.id)
        assert float(stats["received_amount"]) == 20.0
        assert float(stats["pledged_amount"]) == 20.0

        # Keep a direct reference alive so the first insert is not optimized away in future refactors.
        assert int(first.id) > 0


def test_campaign_raised_amount_decreases_on_refund(shared_test_app):
    with shared_test_app.app_context():
        org = _make_org("Campaign Refund Org", "campaign-refund-org")
        campaign = create_campaign(organization_id=org.id, name="Refund Drive", status="active")

        service = DonationService()
        received = service.create_donation(org.id, "Donor Refund", 60.0, status="received", campaign_id=campaign.id)
        service.create_donation(org.id, "Donor Keep", 15.0, status="processed", campaign_id=campaign.id)

        refreshed = get_campaign(campaign.id, org.id)
        assert refreshed is not None
        assert float(refreshed.raised_amount) == 75.0

        service.update_status(received.id, org.id, "refunded")

        refreshed = get_campaign(campaign.id, org.id)
        assert refreshed is not None
        assert float(refreshed.raised_amount) == 15.0

        stats = campaign_stats(campaign.id, org.id)
        assert float(stats["received_amount"]) == 15.0
        assert float(stats["pledged_amount"]) == 15.0


def test_p2p_link_unlink_triggers_campaign_recalculation(shared_test_app, monkeypatch):
    calls: list[tuple[int, int]] = []

    def _capture(campaign_id: int, organization_id: int) -> float:
        calls.append((int(campaign_id), int(organization_id)))
        return 0.0

    monkeypatch.setattr("ngo_homesuite.services.campaign_service.calculate_campaign_total", _capture)

    expected: tuple[int, int]
    with shared_test_app.app_context():
        org = _make_org("Campaign P2P Org", "campaign-p2p-org")
        campaign = Campaign(
            organization_id=org.id,
            name="Community Push",
            slug="community-push",
            campaign_type="p2p",
            status="active",
            goal_amount=500.0,
            raised_amount=0.0,
            currency="USD",
        )
        donor = Donor(organization_id=org.id, name="P2P Owner", email="owner@example.org")
        db.session.add_all([campaign, donor])
        db.session.flush()

        page = p2p_service.create_page(
            organization_id=org.id,
            donor_id=donor.id,
            title="Owner Fundraiser",
            goal_amount=100.0,
        )
        donation = Donation(
            organization_id=org.id,
            donor_name="Supporter",
            donor_email="supporter@example.org",
            amount=20.0,
            currency="USD",
            status="received",
            campaign_id=campaign.id,
        )
        db.session.add(donation)
        db.session.commit()
        expected = (int(campaign.id), int(org.id))

        p2p_service.link_donation(page.id, org.id, donation.id)
        p2p_service.unlink_donation(page.id, org.id, donation.id)

    assert calls.count(expected) == 2
