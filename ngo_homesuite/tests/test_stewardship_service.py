from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import (
    Donor,
    MembershipRecord,
    MembershipTier,
    Organization,
    StewardshipEnrollment,
    StewardshipJourney,
    StewardshipStep,
    db,
)
from ngo_homesuite.services.stewardship_service import _execute_step, process_due_steps, run_auto_enrollments


@pytest.fixture(scope="module")
def app():
    return create_app(TestingConfig)


def test_process_due_steps_counts_email_and_completion(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None

        donor = Donor(
            organization_id=org.id,
            name="Steward Due Donor",
            email="steward.due@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        journey = StewardshipJourney(
            organization_id=org.id,
            name="Stewardship Due Test",
            trigger="new_donor",
            is_active=True,
        )
        db.session.add(journey)
        db.session.flush()

        step = StewardshipStep(
            journey_id=journey.id,
            step_order=0,
            step_type="email",
            delay_days=0,
            subject="Welcome {name}",
            body="Hello {name}",
        )
        db.session.add(step)
        db.session.flush()

        enrollment = StewardshipEnrollment(
            journey_id=journey.id,
            donor_id=donor.id,
            organization_id=org.id,
            status="active",
            current_step=0,
            next_step_due=now - timedelta(minutes=5),
        )
        db.session.add(enrollment)
        db.session.commit()
        enrollment_id = enrollment.id

        outcome = process_due_steps(org.id)
        assert outcome["sent_email"] == 1
        assert outcome["sent_sms"] == 0
        assert outcome["completed"] == 1
        assert outcome["errors"] == 0

        refreshed = db.session.get(StewardshipEnrollment, enrollment_id)
        assert refreshed is not None
        assert refreshed.status == "completed"
        assert refreshed.completed_at is not None


def test_lapsed_member_auto_enrollment_picks_past_end_dates(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None

        donor = Donor(
            organization_id=org.id,
            name="Lapsed Member Donor",
            email="lapsed.member@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        tier = MembershipTier(
            organization_id=org.id,
            name="Lapsed Tier",
            price=25,
            currency="USD",
            interval="annual",
            is_active=True,
        )
        db.session.add(tier)
        db.session.flush()

        record = MembershipRecord(
            organization_id=org.id,
            donor_id=donor.id,
            tier_id=tier.id,
            start_date=(now - timedelta(days=400)).date(),
            end_date=(now - timedelta(days=1)).date(),
            status="lapsed",
        )
        db.session.add(record)
        db.session.flush()

        journey = StewardshipJourney(
            organization_id=org.id,
            name="Lapsed Journey",
            trigger="lapsed_member",
            is_active=True,
        )
        db.session.add(journey)
        db.session.flush()

        step = StewardshipStep(
            journey_id=journey.id,
            step_order=0,
            step_type="wait",
            delay_days=0,
        )
        db.session.add(step)
        db.session.commit()

        first = run_auto_enrollments(org.id)
        second = run_auto_enrollments(org.id)

        assert first["enrolled"] >= 1
        assert second["enrolled"] == 0


def test_process_due_steps_cancels_mismatched_cross_org_donor(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        org_a = Organization.query.filter_by(is_active=True).first()
        assert org_a is not None

        org_b = Organization(name="Stewardship Org B", slug="stewardship-org-b", is_active=True)
        db.session.add(org_b)
        db.session.flush()

        donor_b = Donor(
            organization_id=org_b.id,
            name="Cross Tenant Steward",
            email="cross.steward@example.org",
            donor_type="individual",
        )
        db.session.add(donor_b)
        db.session.flush()

        journey = StewardshipJourney(
            organization_id=org_a.id,
            name="Cross Tenant Journey",
            trigger="new_donor",
            is_active=True,
        )
        db.session.add(journey)
        db.session.flush()

        step = StewardshipStep(
            journey_id=journey.id,
            step_order=0,
            step_type="email",
            delay_days=0,
            subject="Welcome {name}",
            body="Hello {name}",
        )
        db.session.add(step)
        db.session.flush()

        enrollment = StewardshipEnrollment(
            journey_id=journey.id,
            donor_id=donor_b.id,
            organization_id=org_a.id,
            status="active",
            current_step=0,
            next_step_due=now - timedelta(minutes=5),
        )
        db.session.add(enrollment)
        db.session.commit()

        outcome = _execute_step(enrollment)

        assert outcome["sent_email"] == 0
        assert outcome["sent_sms"] == 0
        assert outcome["completed"] == 0

        refreshed = db.session.get(StewardshipEnrollment, enrollment.id)
        assert refreshed is not None
        assert refreshed.status == "cancelled"
