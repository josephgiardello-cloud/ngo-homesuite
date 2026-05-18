from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import (
    Campaign,
    Donation,
    DonationReceipt,
    Donor,
    Expense,
    Fund,
    Organization,
    P2PPage,
    Project,
    RecurringDonationPlan,
    Task,
    db,
)


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        SECRET_KEY = "test-secret"

    return create_app(_TestCfg)


@pytest.fixture()
def client(app):
    return app.test_client()


def _login_admin(client):
    rv = client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin123!"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)


def test_public_give_creates_donation_and_receipt(client, app):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        before = Donation.query.filter_by(organization_id=org.id).count()

    rv = client.post(
        "/give",
        data={
            "donor_name": "Public Supporter",
            "donor_email": "public.supporter@example.org",
            "donor_phone": "+1-555-2000",
            "amount": "25",
            "currency": "USD",
            "payment_method": "credit_card",
            "purpose": "General Fund",
            "make_recurring": "y",
            "recurring_frequency": "monthly",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        after = Donation.query.filter_by(organization_id=org.id).count()
        assert after == before + 1

        donation = Donation.query.filter_by(organization_id=org.id, donor_email="public.supporter@example.org").order_by(Donation.id.desc()).first()
        assert donation is not None

        receipt = DonationReceipt.query.filter_by(donation_id=donation.id).first()
        assert receipt is not None

        plan = RecurringDonationPlan.query.filter_by(organization_id=org.id, donor_id=donation.donor_id).order_by(RecurringDonationPlan.id.desc()).first()
        assert plan is not None
        assert plan.status == "active"


def test_public_p2p_page_renders_html_and_json(client, app):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="P2P Public Donor",
            email="p2p.public@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title="Spring Field Kits",
            story="Funding community field kits for youth volunteers.",
            goal_amount=500.0,
            match_ratio=1.0,
            match_cap_amount=250.0,
            challenge_goal_amount=800.0,
            public_slug="spring-field-kits",
            status="active",
        )
        db.session.add(page)
        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=75.0,
            currency="USD",
            payment_method="cash",
            purpose="P2P: Spring Field Kits",
            status="received",
        )
        db.session.add(donation)
        db.session.flush()
        page.donations.append(donation)
        db.session.commit()

    html_resp = client.get("/p2p/spring-field-kits", headers={"Accept": "text/html"})
    assert html_resp.status_code == 200
    html_body = html_resp.get_data(as_text=True)
    assert "Spring Field Kits" in html_body
    assert "Support this fundraiser" in html_body
    assert "Embed This Fundraiser" in html_body
    assert "Recent Supporters" in html_body
    assert "Share This Fundraiser" in html_body
    assert "Matching Challenge Active" in html_body
    assert "Supporter Messages" in html_body

    json_resp = client.get("/p2p/spring-field-kits", headers={"Accept": "application/json"})
    assert json_resp.status_code == 200
    payload = json_resp.get_json()
    assert payload["title"] == "Spring Field Kits"
    assert "progress" in payload


def test_public_p2p_embed_script_endpoint(client, app):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="P2P Embed Donor",
            email="p2p.embed@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title="Embed Fundraiser",
            goal_amount=300.0,
            public_slug="embed-fundraiser",
            status="active",
        )
        db.session.add(page)
        db.session.commit()

    rv = client.get("/p2p/embed-fundraiser/embed.js")
    assert rv.status_code == 200
    assert rv.mimetype == "application/javascript"
    body = rv.get_data(as_text=True)
    assert "createElement('iframe')" in body
    assert "/p2p/embed-fundraiser?embed=1" in body


def test_public_p2p_embed_script_escapes_title_for_js_context(client, app):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="P2P Embed Safe Donor",
            email="p2p.embed.safe@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title='Bad\"Title\';alert(1);//',
            goal_amount=300.0,
            public_slug="embed-fundraiser-safe",
            status="active",
        )
        db.session.add(page)
        db.session.commit()

    rv = client.get("/p2p/embed-fundraiser-safe/embed.js")
    assert rv.status_code == 200
    assert rv.mimetype == "application/javascript"
    body = rv.get_data(as_text=True)
    title_line = next(line.strip() for line in body.splitlines() if "iframe.title =" in line)
    assert title_line.startswith('iframe.title = "Fundraiser: ')
    assert title_line.endswith('";')
    assert "iframe.title = 'Fundraiser:" not in body
    assert "alert(1);" not in body.split("iframe.title =", 1)[0]


def test_public_p2p_embed_script_ignores_host_header_in_iframe_src(client, app):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="P2P Host Header Donor",
            email="p2p.host.header@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title="Host Header Safety",
            goal_amount=300.0,
            public_slug="embed-host-header-safety",
            status="active",
        )
        db.session.add(page)
        db.session.commit()

    rv = client.get(
        "/p2p/embed-host-header-safety/embed.js",
        headers={"Host": "evil.example"},
    )
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert 'iframe.src = "/p2p/embed-host-header-safety?embed=1";' in body
    assert "evil.example" not in body


def test_p2p_leaderboard_clamps_limit_and_offset(client, app, monkeypatch):
    _login_admin(client)

    import ngo_homesuite.services.p2p_service as p2p_service

    captured: dict[str, int | str | None] = {}

    def fake_leaderboard(org_id: int, campaign_slug=None, limit: int = 10, offset: int = 0):
        captured["org_id"] = org_id
        captured["campaign_slug"] = campaign_slug
        captured["limit"] = limit
        captured["offset"] = offset
        return []

    monkeypatch.setattr(p2p_service, "leaderboard", fake_leaderboard)

    rv = client.get("/p2p/leaderboard?limit=99999&offset=-7")
    assert rv.status_code == 200
    assert captured["limit"] == 100
    assert captured["offset"] == 0


def test_staff_p2p_manage_page_create_publish_close(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        org_id = int(org.id)
        donor = Donor(
            organization_id=org_id,
            name="P2P Staff Owner",
            email="p2p.staff.owner@example.org",
            donor_type="individual",
        )
        donor_alt = Donor(
            organization_id=org_id,
            name="P2P Alternate Owner",
            email="p2p.alt.owner@example.org",
            donor_type="individual",
        )
        db.session.add_all([donor, donor_alt])
        db.session.commit()
        donor_id = donor.id
        donor_alt_id = donor_alt.id

    manage = client.get("/p2p/manage")
    assert manage.status_code == 200
    assert "Create Fundraiser" in manage.get_data(as_text=True)
    assert "Fundraiser Pages" in manage.get_data(as_text=True)

    create_resp = client.post(
        "/p2p/manage",
        data={
            "donor_id": str(donor_id),
            "title": "Staff Managed Page",
            "goal_amount": "900",
            "story": "A page created through the staff dashboard.",
            "campaign_slug": "staff-managed-campaign",
            "match_ratio": "1",
            "match_cap_amount": "300",
            "challenge_goal_amount": "1200",
            "automation_contact_email": "p2p.staff.owner@example.org",
        },
        follow_redirects=True,
    )
    assert create_resp.status_code == 200

    filtered_manage = client.get("/p2p/manage?q=staff&status=active&sort_by=raised&sort_dir=desc")
    assert filtered_manage.status_code == 200
    filtered_body = filtered_manage.get_data(as_text=True)
    assert "Apply" in filtered_body
    assert "Copy Link" in filtered_body
    assert "Automation Queue" in filtered_body

    with app.app_context():
        page = P2PPage.query.filter_by(title="Staff Managed Page", organization_id=org_id).first()
        assert page is not None
        page_id = int(page.id)

    publish_resp = client.post(
        "/p2p/manage",
        data={"action": "publish", "page_id": str(page_id)},
        follow_redirects=True,
    )
    assert publish_resp.status_code == 200

    with app.app_context():
        db.session.expire_all()
        page = db.session.get(P2PPage, page_id)
        assert page.status == "active"

    update_resp = client.post(
        "/p2p/manage",
        data={
            "action": "update",
            "page_id": str(page_id),
            "title": "Staff Managed Page Updated",
            "goal_amount": "1200",
            "story": "Updated story",
            "campaign_slug": "staff-managed-campaign",
            "match_ratio": "2",
            "match_cap_amount": "400",
            "challenge_goal_amount": "1800",
            "automation_contact_email": "updated.owner@example.org",
        },
        follow_redirects=True,
    )
    assert update_resp.status_code == 200

    reassign_resp = client.post(
        "/p2p/manage",
        data={
            "action": "reassign_owner",
            "page_id": str(page_id),
            "new_owner_id": str(donor_alt_id),
        },
        follow_redirects=True,
    )
    assert reassign_resp.status_code == 200

    bulk_resp = client.post(
        "/p2p/manage",
        data={
            "action": "bulk_status",
            "bulk_target_status": "closed",
            "selected_page_ids": [str(page_id)],
        },
        follow_redirects=True,
    )
    assert bulk_resp.status_code == 200

    close_resp = client.post(
        "/p2p/manage",
        data={"action": "close", "page_id": str(page_id)},
        follow_redirects=True,
    )
    assert close_resp.status_code == 200

    with app.app_context():
        db.session.expire_all()
        page = db.session.get(P2PPage, page_id)
        assert page.status == "closed"
        assert page.title == "Staff Managed Page Updated"
        assert int(page.donor_id) == int(donor_alt_id)


def test_task_board_page_renders_for_authenticated_user(client):
    _login_admin(client)

    rv = client.get('/tasks/board')
    assert rv.status_code == 200

    body = rv.get_data(as_text=True)
    assert 'Task Board' in body
    assert 'Reminder Candidates' in body
    assert 'Grant Operations' in body
    assert 'Upcoming Grant Milestones' in body


def test_activity_feed_page_renders_grant_operations_pulse(client):
    _login_admin(client)

    rv = client.get('/activity')
    assert rv.status_code == 200

    body = rv.get_data(as_text=True)
    assert 'Activity Feed' in body
    assert 'Grant Operations Pulse' in body


def test_donor_merge_relinks_donations(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()

        primary = Donor(organization_id=org.id, name="Merge Target", email="merge.target@example.org", donor_type="individual")
        duplicate = Donor(organization_id=org.id, name="Merge Target", email="merge.target@example.org", donor_type="individual")
        db.session.add_all([primary, duplicate])
        db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=duplicate.id,
            donor_name=duplicate.name,
            donor_email=duplicate.email,
            amount=33.0,
            currency="USD",
            status="received",
            payment_method="bank_transfer",
        )
        db.session.add(donation)
        db.session.commit()

        primary_id = primary.id
        duplicate_id = duplicate.id
        donation_id = donation.id

    rv = client.post(
        "/donors/merge",
        data={"primary_id": str(primary_id), "duplicate_id": str(duplicate_id)},
        follow_redirects=True,
    )
    assert rv.status_code == 200

    with app.app_context():
        assert Donor.query.filter_by(id=duplicate_id).first() is None
        merged_donation = Donation.query.filter_by(id=donation_id).first()
        assert merged_donation.donor_id == primary_id


def test_process_recurring_marks_failure_without_email(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(organization_id=org.id, name="No Email Recurring", email=None, donor_type="individual")
        db.session.add(donor)
        db.session.flush()

        plan = RecurringDonationPlan(
            organization_id=org.id,
            donor_id=donor.id,
            amount=19.0,
            currency="USD",
            frequency="monthly",
            payment_method="credit_card",
            next_charge_date=date.today(),
            status="active",
        )
        db.session.add(plan)
        db.session.commit()
        plan_id = plan.id

    rv = client.post("/donations/recurring/process", follow_redirects=True)
    assert rv.status_code == 200

    with app.app_context():
        refreshed = db.session.get(RecurringDonationPlan, plan_id)
        assert refreshed.status == "failed"
        assert refreshed.fail_count >= 1
        assert refreshed.last_error


def test_process_recurring_creates_donation_and_receipt(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Recurring Success",
            email="recurring.success@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        plan = RecurringDonationPlan(
            organization_id=org.id,
            donor_id=donor.id,
            amount=27.0,
            currency="USD",
            frequency="monthly",
            payment_method="credit_card",
            purpose="Recurring Success Campaign",
            next_charge_date=date.today(),
            status="active",
        )
        db.session.add(plan)
        db.session.commit()
        plan_id = plan.id

    rv = client.post("/donations/recurring/process", follow_redirects=True)
    assert rv.status_code == 200

    with app.app_context():
        refreshed = db.session.get(RecurringDonationPlan, plan_id)
        assert refreshed.status == "active"
        assert refreshed.fail_count == 0
        assert refreshed.last_error is None

        donation = (
            Donation.query.filter_by(
                organization_id=refreshed.organization_id,
                donor_id=refreshed.donor_id,
                purpose="Recurring Success Campaign",
            )
            .order_by(Donation.id.desc())
            .first()
        )
        assert donation is not None
        assert donation.status == "receipted"

        receipt = DonationReceipt.query.filter_by(donation_id=donation.id).first()
        assert receipt is not None


def test_donor_detail_page_shows_profile_metrics(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Detail Donor",
            email="detail.donor@example.org",
            donor_type="individual",
            notes="Long-time supporter",
        )
        db.session.add(donor)
        db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=55.0,
            currency="USD",
            status="received",
            payment_method="bank_transfer",
            purpose="Education",
        )
        db.session.add(donation)

        plan = RecurringDonationPlan(
            organization_id=org.id,
            donor_id=donor.id,
            amount=15.0,
            currency="USD",
            frequency="monthly",
            payment_method="credit_card",
            next_charge_date=date.today(),
            status="active",
        )
        db.session.add(plan)
        db.session.commit()
        donor_id = donor.id

    rv = client.get(f"/donors/{donor_id}")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Detail Donor" in body
    assert "Long-time supporter" in body
    assert "AI Donor Insights" in body
    assert "Next Best Action:" in body
    assert "Recent Donations" in body
    assert "Recurring Plans" in body
    assert "Section Visibility" in body
    assert "Household & Relationships" in body
    assert "Segments, Tags & Custom Fields" in body
    assert "Stewardship Tasks" in body
    assert "Communication History" in body
    assert "Quick Stewardship Task" in body
    assert "Custom Field Values" in body
    assert "donor-detail-visibility-v1:user-" in body
    assert ":donor-" in body


def test_donor_detail_quick_task_create(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        org_id = org.id
        donor = Donor(
            organization_id=org.id,
            name="Task Donor",
            email="task.donor@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()
        donor_id = donor.id

    rv = client.post(
        f"/donors/{donor_id}",
        data={
            "task-title": "Call donor for stewardship update",
            "task-task_type": "call",
            "task-priority": "high",
            "task-due_date": date.today().isoformat(),
            "task-notes": "Discuss annual impact report.",
            "task-submit": "Create Task",
        },
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)

    with app.app_context():
        task = (
            Task.query.filter_by(organization_id=org_id, donor_id=donor_id)
            .order_by(Task.id.desc())
            .first()
        )
        assert task is not None
        assert task.title == "Call donor for stewardship update"
        assert task.task_type == "call"
        assert task.priority == "high"


def test_donor_create_accepts_photo_upload(client, app):
    _login_admin(client)

    rv = client.post(
        "/donors/new",
        data={
            "name": "Photo Donor",
            "email": "photo.donor@example.org",
            "phone": "+1-555-3000",
            "donor_type": "individual",
            "notes": "Uploaded profile photo",
            "photo": (BytesIO(b"fake-jpeg-bytes"), "donor.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)

    with app.app_context():
        donor = Donor.query.filter_by(email="photo.donor@example.org").first()
        assert donor is not None
        assert donor.photo_path
        file_path = Path(app.instance_path) / str(donor.photo_path)
        assert file_path.exists()


def test_donations_page_handles_malformed_amount_rows(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Null Amount Donor",
            email="null.amount@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        # Simulate legacy SQLite drift where amount is persisted as non-numeric text.
        conn = db.session.connection()
        conn.exec_driver_sql(
            """
            INSERT INTO donations (
                organization_id, donor_id, donor_name, donor_email,
                amount, currency, donation_date, status, purpose,
                payment_method, is_anonymous, created_at, updated_at, version_id
            )
            VALUES (
                :organization_id, :donor_id, :donor_name, :donor_email,
                :amount, :currency, CURRENT_TIMESTAMP, :status, :purpose,
                :payment_method, :is_anonymous, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :version_id
            )
            """,
            {
                'organization_id': int(org.id),
                'donor_id': int(donor.id),
                'donor_name': donor.name,
                'donor_email': donor.email,
                'amount': 'legacy-bad-value',
                'currency': 'USD',
                'status': 'received',
                'purpose': 'Legacy import',
                'payment_method': 'bank_transfer',
                'is_anonymous': 0,
                'version_id': 0,
            },
        )
        db.session.commit()

    rv = client.get("/donations")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Null Amount Donor" in body
    assert "USD 0.00" in body


def test_donations_export_iif_returns_payload(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="IIF Donor",
            email="iif.donor@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=42.5,
            currency="USD",
            status="received",
            purpose="IIF Export",
            payment_method="bank_transfer",
        )
        db.session.add(donation)
        db.session.commit()

    rv = client.get("/donations/export/iif")
    assert rv.status_code == 200
    assert rv.mimetype == "text/plain"
    body = rv.get_data(as_text=True)
    assert "!TRNS" in body
    assert "DEPOSIT" in body


def test_donations_page_supports_advanced_filters_sort_and_pagination(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Filter Sort Donor",
            email="filter.sort@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        for idx in range(3):
            db.session.add(
                Donation(
                    organization_id=org.id,
                    donor_id=donor.id,
                    donor_name=donor.name,
                    donor_email=donor.email,
                    amount=50 + idx,
                    currency="USD",
                    status="received",
                    payment_method="bank_transfer",
                    purpose=f"Filter batch {idx}",
                )
            )
        db.session.commit()

    rv = client.get(
        "/donations?currency=USD&donor_type=individual&sort_by=amount&sort_dir=asc&per_page=25&page=1",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Rows / Page" in body
    assert "Filter Sort Donor" in body
    assert "Advanced Filters & View Settings" in body
    assert "Bulk Status" in body
    assert "Channel" in body
    assert "Update Status" in body


def test_donation_create_persists_campaign_channel_and_tribute_fields(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="CRM Field Donor",
            email="crm.fields@example.org",
            donor_type="individual",
        )
        campaign = Campaign(
            organization_id=org.id,
            name="Campaign For Donation Form",
            slug="campaign-for-donation-form",
            status="active",
        )
        db.session.add_all([donor, campaign])
        db.session.commit()
        donor_id = donor.id
        campaign_id = campaign.id

    rv = client.post(
        "/donations/new",
        data={
            "donor_id": str(donor_id),
            "campaign_id": str(campaign_id),
            "project_id": "0",
            "fund_id": "0",
            "amount": "125.50",
            "currency": "USD",
            "payment_method": "credit_card",
            "channel": "event",
            "is_anonymous": "y",
            "public_display_name": "Anonymous Champion",
            "tribute_type": "in_honor_of",
            "tribute_honoree_name": "Coach Rivera",
            "tribute_honoree_contact": "coach.rivera@example.org",
            "soft_credit_name": "Team Sponsor",
            "purpose": "Scholarship Program",
            "reference_number": "CRM-001",
        },
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donation = (
            Donation.query.filter_by(organization_id=org.id, donor_id=donor_id)
            .order_by(Donation.id.desc())
            .first()
        )
        assert donation is not None
        assert donation.campaign_id == campaign_id
        assert donation.channel == "event"
        assert donation.is_anonymous is True
        assert donation.public_display_name == "Anonymous Champion"
        assert donation.tribute_type == "in_honor_of"
        assert donation.tribute_honoree_name == "Coach Rivera"
        assert donation.tribute_honoree_contact == "coach.rivera@example.org"
        assert donation.soft_credit_name == "Team Sponsor"


def test_donation_form_has_collapsible_advanced_attribution_section(client):
    _login_admin(client)

    rv = client.get("/donations/new")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Advanced Attribution (Optional)" in body
    assert "Tribute Type" in body
    assert "Soft Credit Name" in body


def test_bulk_donation_actions_update_status_and_resend_receipts(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Bulk Action Donor",
            email="bulk.action@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        donation_a = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=60.0,
            currency="USD",
            status="received",
            payment_method="bank_transfer",
            purpose="Bulk status A",
        )
        donation_b = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=70.0,
            currency="USD",
            status="received",
            payment_method="credit_card",
            purpose="Bulk status B",
        )
        db.session.add_all([donation_a, donation_b])
        db.session.commit()
        donation_ids = [donation_a.id, donation_b.id]

    rv = client.post(
        "/donations/bulk/status",
        data={
            "donation_ids": [str(donation_ids[0]), str(donation_ids[1])],
            "new_status": "processed",
            "next_url": "/donations",
        },
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)

    rv2 = client.post(
        "/donations/bulk/receipt/resend",
        data={
            "donation_ids": [str(donation_ids[0]), str(donation_ids[1])],
            "next_url": "/donations",
        },
        follow_redirects=False,
    )
    assert rv2.status_code in (302, 303)

    with app.app_context():
        for donation_id in donation_ids:
            refreshed = db.session.get(Donation, donation_id)
            assert refreshed is not None
            assert refreshed.status in {"processed", "receipted"}
            assert DonationReceipt.query.filter_by(donation_id=donation_id).first() is not None


def test_donation_row_status_update_and_receipt_resend_actions(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Action Donor",
            email="action.donor@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=88.0,
            currency="USD",
            status="received",
            payment_method="credit_card",
            purpose="Action flow",
        )
        db.session.add(donation)
        db.session.commit()
        donation_id = donation.id

    rv = client.post(
        f"/donations/{donation_id}/status",
        data={
            'donation_id': str(donation_id),
            'new_status': 'processed',
            'next_url': '/donations',
        },
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)

    with app.app_context():
        refreshed = db.session.get(Donation, donation_id)
        assert refreshed is not None
        assert refreshed.status == 'processed'

    rv2 = client.post(
        f"/donations/{donation_id}/receipt/resend",
        data={
            'donation_id': str(donation_id),
            'next_url': '/donations',
        },
        follow_redirects=False,
    )
    assert rv2.status_code in (302, 303)


def test_expenses_export_iif_returns_payload(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()

        expense = Expense(
            organization_id=org.id,
            amount=18.75,
            currency="USD",
            payee="IIF Vendor",
            description="IIF Expense Export",
        )
        db.session.add(expense)
        db.session.commit()

    rv = client.get("/expenses/export/iif")
    assert rv.status_code == 200
    assert rv.mimetype == "text/plain"
    body = rv.get_data(as_text=True)
    assert "!TRNS" in body
    assert "CHECK" in body


def test_projects_page_and_exports_render(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        project = Project(
            organization_id=org.id,
            name="Project Service Migration",
            description="Project route service test",
            program="Core Programs",
            budget=1000.0,
            spent=250.0,
            currency="USD",
            status="active",
        )
        db.session.add(project)
        db.session.commit()

    rv = client.get("/projects")
    assert rv.status_code == 200
    assert "Project Service Migration" in rv.get_data(as_text=True)

    csv_rv = client.get("/projects/export/csv")
    assert csv_rv.status_code == 200
    assert csv_rv.mimetype == "text/csv"

    xlsx_rv = client.get("/projects/export/xlsx")
    assert xlsx_rv.status_code == 200
    assert xlsx_rv.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_funds_page_renders_kpis_and_rollup_metrics(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        fund = Fund(
            organization_id=org.id,
            name="Education Relief Fund",
            description="Dedicated to student support",
            is_active=True,
        )
        donor = Donor(
            organization_id=org.id,
            name="Funds Metrics Donor",
            email="funds.metrics@example.org",
            donor_type="individual",
        )
        db.session.add_all([fund, donor])
        db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            fund_id=fund.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=150.0,
            currency="USD",
            status="received",
            payment_method="bank_transfer",
        )
        expense = Expense(
            organization_id=org.id,
            fund_id=fund.id,
            amount=40.0,
            currency="USD",
            payee="Stationery Vendor",
            description="Supplies",
        )
        db.session.add_all([donation, expense])
        db.session.commit()

    rv = client.get("/funds?q=Education&status=active")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Visible Funds" in body
    assert "Net Position" in body
    assert "Education Relief Fund" in body
    assert "In: $150.00" in body
    assert "Out: $40.00" in body
    assert "Net: $110.00" in body
    assert "Rows / Page" in body
    assert "With Transactions" in body
    assert "Deactivate" in body
    assert "6 month trend sparkline" in body
    assert "/donations?fund_id=" in body
    assert "/expenses?fund_id=" in body


def test_fund_form_renders_polished_guidance(client):
    _login_admin(client)

    rv = client.get("/funds/new")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Create a fund to organize donations and expenses" in body
    assert "Fund Naming Tips" in body
    assert "Set inactive instead of deleting" in body


def test_funds_page_supports_activity_filter_sort_and_pagination(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()

        fund_a = Fund(organization_id=org.id, name="Alpha Activity Fund", is_active=True)
        fund_b = Fund(organization_id=org.id, name="Beta Quiet Fund", is_active=False)
        donor = Donor(
            organization_id=org.id,
            name="Fund Filter Donor",
            email="fund.filter@example.org",
            donor_type="individual",
        )
        db.session.add_all([fund_a, fund_b, donor])
        db.session.flush()

        db.session.add(
            Donation(
                organization_id=org.id,
                donor_id=donor.id,
                fund_id=fund_a.id,
                donor_name=donor.name,
                donor_email=donor.email,
                amount=25.0,
                currency="USD",
                status="received",
                payment_method="cash",
            )
        )
        db.session.commit()

    rv = client.get(
        "/funds?has_activity=with&sort_by=net&sort_dir=desc&per_page=25&page=1",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Alpha Activity Fund" in body
    assert "Beta Quiet Fund" not in body
    assert "Page 1 of" in body


def test_fund_quick_status_update_from_funds_list(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        fund = Fund(
            organization_id=org.id,
            name="Quick Status Fund",
            description="Toggle status from list",
            is_active=True,
        )
        db.session.add(fund)
        db.session.commit()
        fund_id = int(fund.id)

    rv = client.post(
        f"/funds/{fund_id}/status",
        data={
            "fund_id": str(fund_id),
            "set_status": "inactive",
            "next_url": "/funds",
        },
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)

    with app.app_context():
        refreshed = db.session.get(Fund, fund_id)
        assert refreshed is not None
        assert refreshed.is_active is False


def test_funds_bulk_status_update_from_list(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        fund_a = Fund(organization_id=org.id, name="Bulk Fund A", is_active=True)
        fund_b = Fund(organization_id=org.id, name="Bulk Fund B", is_active=True)
        db.session.add_all([fund_a, fund_b])
        db.session.commit()
        fund_a_id = int(fund_a.id)
        fund_b_id = int(fund_b.id)

    rv = client.post(
        "/funds/bulk/status",
        data={
            "fund_ids": [str(fund_a_id), str(fund_b_id)],
            "set_status": "inactive",
            "next_url": "/funds",
        },
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)

    with app.app_context():
        refreshed_a = db.session.get(Fund, fund_a_id)
        refreshed_b = db.session.get(Fund, fund_b_id)
        assert refreshed_a is not None and refreshed_a.is_active is False
        assert refreshed_b is not None and refreshed_b.is_active is False


def test_fund_drilldown_filters_donations_and_expenses(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Drilldown Donor",
            email="drilldown.donor@example.org",
            donor_type="individual",
        )
        fund_a = Fund(organization_id=org.id, name="Drilldown Fund A", is_active=True)
        fund_b = Fund(organization_id=org.id, name="Drilldown Fund B", is_active=True)
        db.session.add_all([donor, fund_a, fund_b])
        db.session.flush()

        db.session.add_all(
            [
                Donation(
                    organization_id=org.id,
                    donor_id=donor.id,
                    fund_id=fund_a.id,
                    donor_name=donor.name,
                    donor_email=donor.email,
                    amount=90.0,
                    currency="USD",
                    status="received",
                    payment_method="cash",
                    purpose="Fund A Donation",
                ),
                Donation(
                    organization_id=org.id,
                    donor_id=donor.id,
                    fund_id=fund_b.id,
                    donor_name=donor.name,
                    donor_email=donor.email,
                    amount=55.0,
                    currency="USD",
                    status="received",
                    payment_method="cash",
                    purpose="Fund B Donation",
                ),
                Expense(
                    organization_id=org.id,
                    fund_id=fund_a.id,
                    amount=23.0,
                    currency="USD",
                    payee="Fund A Vendor",
                    description="Fund A Expense",
                ),
                Expense(
                    organization_id=org.id,
                    fund_id=fund_b.id,
                    amount=19.0,
                    currency="USD",
                    payee="Fund B Vendor",
                    description="Fund B Expense",
                ),
            ]
        )
        db.session.commit()
        fund_a_id = int(fund_a.id)

    donations_rv = client.get(f"/donations?fund_id={fund_a_id}")
    assert donations_rv.status_code == 200
    donations_body = donations_rv.get_data(as_text=True)
    assert "Fund A Donation" in donations_body
    assert "Fund B Donation" not in donations_body

    expenses_rv = client.get(f"/expenses?fund_id={fund_a_id}")
    assert expenses_rv.status_code == 200
    expenses_body = expenses_rv.get_data(as_text=True)
    assert "Fund A Expense" in expenses_body
    assert "Fund B Expense" not in expenses_body


def test_funds_page_supports_trend_window_presets(client, app):
    _login_admin(client)

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        donor = Donor(
            organization_id=org.id,
            name="Trend Preset Donor",
            email="trend.preset@example.org",
            donor_type="individual",
        )
        fund = Fund(
            organization_id=org.id,
            name="Trend Window Fund",
            is_active=True,
        )
        db.session.add_all([donor, fund])
        db.session.flush()

        db.session.add(
            Donation(
                organization_id=org.id,
                donor_id=donor.id,
                fund_id=fund.id,
                donor_name=donor.name,
                donor_email=donor.email,
                amount=70.0,
                currency="USD",
                status="received",
                payment_method="cash",
            )
        )
        db.session.commit()

    rv = client.get("/funds?trend_window=30d")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Trend Window" in body
    assert "Last 30d" in body
    assert "6 month trend sparkline" in body


def test_project_create_and_edit_flow(client, app):
    _login_admin(client)

    create_rv = client.post(
        "/projects/new",
        data={
            "name": "Created Via Route",
            "description": "Created in test",
            "program": "Ops",
            "budget": "500",
            "spent": "50",
            "currency": "USD",
            "status": "planned",
        },
        follow_redirects=True,
    )
    assert create_rv.status_code == 200

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        project = Project.query.filter_by(organization_id=org.id, name="Created Via Route").first()
        assert project is not None
        project_id = project.id

    edit_rv = client.post(
        f"/projects/{project_id}/edit",
        data={
            "name": "Created Via Route",
            "description": "Updated in test",
            "program": "Ops",
            "budget": "600",
            "spent": "100",
            "currency": "USD",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert edit_rv.status_code == 200

    with app.app_context():
        refreshed = db.session.get(Project, project_id)
        assert refreshed.description == "Updated in test"
        assert float(refreshed.budget) == 600.0
        assert refreshed.status == "active"


def test_reports_page_renders_with_financial_totals(client, app):
    _login_admin(client)

    rv = client.get("/reports")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Reports" in body
    assert "Campaign Email Workbench" in body
    assert "Generate AI Draft" in body
    assert "External Communications Authorization Audit" in body
    assert "/api/v2/campaigns/" in body


def test_campaign_email_workbench_route_renders_dedicated_view(client):
    _login_admin(client)

    rv = client.get("/campaigns/email-workbench")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Campaign Email Workbench" in body
    assert "Human Reviewer Name" in body
    assert "Special warning" in body
    assert "Email Workbench" in body


def test_settings_page_renders_custom_fields_schema_admin_ui(client):
    _login_admin(client)

    rv = client.get("/settings")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Custom Fields Schema" in body
    assert "/admin/custom-fields/schema" in body
    assert "Email Integration Smoke Check" in body
    assert "/integrations/email/smoke" in body
