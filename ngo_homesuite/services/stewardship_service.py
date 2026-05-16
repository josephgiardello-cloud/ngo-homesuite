"""Stewardship journey execution engine.

Processes active enrollments and dispatches due steps (email / SMS / wait).
Designed to be called from a cron job or scheduled Flask-APScheduler task.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select

from ngo_homesuite.models.core import (
    Donor,
    StewardshipEnrollment,
    StewardshipJourney,
    StewardshipStep,
    db,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

def enroll_donor(
    organization_id: int,
    donor_id: int,
    journey_id: int,
) -> Optional[StewardshipEnrollment]:
    """Enroll a donor in a journey.  No-ops if already enrolled and active."""
    journey = db.session.scalars(
        select(StewardshipJourney).where(
            StewardshipJourney.id == journey_id,
            StewardshipJourney.organization_id == organization_id,
            StewardshipJourney.is_active == True,
        ).limit(1)
    ).first()
    if not journey:
        logger.warning("Journey %s not found or inactive", journey_id)
        return None

    existing = db.session.scalars(
        select(StewardshipEnrollment).where(
            StewardshipEnrollment.journey_id == journey_id,
            StewardshipEnrollment.donor_id == donor_id,
            StewardshipEnrollment.organization_id == organization_id,
            StewardshipEnrollment.status == "active",
        ).limit(1)
    ).first()
    if existing:
        return existing  # already enrolled

    first_step = journey.steps[0] if journey.steps else None
    enrollment = StewardshipEnrollment(
        journey_id=journey_id,
        donor_id=donor_id,
        organization_id=organization_id,
        current_step=0,
        status="active",
        next_step_due=_utcnow() + timedelta(days=first_step.delay_days if first_step else 0),
    )
    db.session.add(enrollment)
    db.session.commit()
    logger.info("Enrolled donor %s in journey %s", donor_id, journey_id)
    return enrollment


def cancel_enrollment(enrollment_id: int, organization_id: int) -> None:
    enr = db.session.scalars(
        select(StewardshipEnrollment).where(
            StewardshipEnrollment.id == enrollment_id,
            StewardshipEnrollment.organization_id == organization_id,
        ).limit(1)
    ).first()
    if enr:
        enr.status = "cancelled"
        db.session.commit()


# ---------------------------------------------------------------------------
# Auto-enroll by trigger
# ---------------------------------------------------------------------------

TRIGGER_MAP = {
    "new_donor": lambda org_id: _new_donors(org_id),
    "lybunt": lambda org_id: _lybunt_donors(org_id),
    "lapsed_member": lambda org_id: _lapsed_members(org_id),
}


def _new_donors(org_id: int) -> List[int]:
    from datetime import timedelta
    cutoff = _utcnow() - timedelta(days=7)
    rows = list(db.session.scalars(
        select(Donor).where(
            Donor.organization_id == org_id,
            Donor.created_at >= cutoff,
        )
    ))
    return [d.id for d in rows]


def _lybunt_donors(org_id: int) -> List[int]:
    from ngo_homesuite.services.advanced_reporting_service import get_lybunt_donors
    rows = get_lybunt_donors(org_id)
    return [r["donor_id"] for r in rows]


def _lapsed_members(org_id: int) -> List[int]:
    from ngo_homesuite.models.core import MembershipRecord
    from datetime import timezone
    today = datetime.now(timezone.utc).date()
    rows = list(db.session.scalars(
        select(MembershipRecord).where(
            MembershipRecord.organization_id == org_id,
            MembershipRecord.status == "lapsed",
            MembershipRecord.end_date < today,
        )
    ))
    return [r.donor_id for r in rows]


def run_auto_enrollments(organization_id: int) -> dict:
    """Find all active journeys with triggers and enroll matching donors."""
    journeys = list(db.session.scalars(
        select(StewardshipJourney).where(
            StewardshipJourney.organization_id == organization_id,
            StewardshipJourney.is_active == True,
        )
    ))
    enrolled = 0
    for journey in journeys:
        if journey.trigger not in TRIGGER_MAP:
            continue
        donor_ids = TRIGGER_MAP[journey.trigger](organization_id)
        for did in donor_ids:
            existing = db.session.scalars(
                select(StewardshipEnrollment).where(
                    StewardshipEnrollment.journey_id == journey.id,
                    StewardshipEnrollment.donor_id == did,
                    StewardshipEnrollment.organization_id == organization_id,
                    StewardshipEnrollment.status == "active",
                ).limit(1)
            ).first()
            result = enroll_donor(organization_id, did, journey.id)
            if result and existing is None:
                enrolled += 1
    return {"enrolled": enrolled}


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------

def process_due_steps(organization_id: int) -> dict:
    """Execute all due enrollment steps for an org. Call from scheduler."""
    now = _utcnow()
    due = list(db.session.scalars(
        select(StewardshipEnrollment).where(
            StewardshipEnrollment.organization_id == organization_id,
            StewardshipEnrollment.status == "active",
            StewardshipEnrollment.next_step_due <= now,
        )
    ))

    sent_email = 0
    sent_sms = 0
    completed = 0
    errors = 0

    for enrollment in due:
        try:
            outcome = _execute_step(enrollment)
            sent_email += int(outcome.get("sent_email", 0))
            sent_sms += int(outcome.get("sent_sms", 0))
            completed += int(outcome.get("completed", 0))
        except Exception as exc:  # noqa: BLE001
            logger.error("Step execution failed enrollment=%s: %s", enrollment.id, exc)
            errors += 1

    db.session.commit()
    return {"sent_email": sent_email, "sent_sms": sent_sms, "completed": completed, "errors": errors}


def _execute_step(enrollment: StewardshipEnrollment) -> dict:
    outcome = {"sent_email": 0, "sent_sms": 0, "completed": 0}
    journey = enrollment.journey
    steps: List[StewardshipStep] = journey.steps  # ordered by step_order

    if enrollment.current_step >= len(steps):
        enrollment.status = "completed"
        enrollment.completed_at = _utcnow()
        outcome["completed"] = 1
        return outcome

    step = steps[enrollment.current_step]
    donor = db.session.scalars(
        select(Donor).where(
            Donor.id == enrollment.donor_id,
            Donor.organization_id == enrollment.organization_id,
        ).limit(1)
    ).first()
    if not donor:
        enrollment.status = "cancelled"
        return outcome

    if step.step_type == "email":
        _dispatch_email(donor, step)
        outcome["sent_email"] = 1
    elif step.step_type == "sms":
        _dispatch_sms(donor, step)
        outcome["sent_sms"] = 1
    # "wait" steps just advance the pointer

    # Advance to next step
    enrollment.current_step += 1
    if enrollment.current_step >= len(steps):
        enrollment.status = "completed"
        enrollment.completed_at = _utcnow()
        enrollment.next_step_due = None
        outcome["completed"] = 1
    else:
        next_step = steps[enrollment.current_step]
        enrollment.next_step_due = _utcnow() + timedelta(days=max(0, next_step.delay_days))
    return outcome


def _dispatch_email(donor: Donor, step: StewardshipStep) -> None:
    if not donor.email:
        return
    try:
        import smtplib
        import os
        from email.message import EmailMessage

        subject = (step.subject or "A message from us").replace("{name}", donor.name)
        body = (step.body or "").replace("{name}", donor.name).replace("{email}", donor.email or "")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.getenv("EMAIL_FROM", "noreply@homesuite.local")
        msg["To"] = donor.email
        msg.set_content(body)

        smtp_host = os.getenv("SMTP_HOST")
        if smtp_host:
            with smtplib.SMTP(smtp_host, int(os.getenv("SMTP_PORT", "587"))) as server:
                server.starttls()
                server.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", ""))
                server.send_message(msg)
        else:
            logger.info("[EMAIL STUB] to=%s subject=%s", donor.email, subject)
    except Exception as exc:  # noqa: BLE001
        logger.error("Email dispatch failed donor=%s: %s", donor.id, exc)


def _dispatch_sms(donor: Donor, step: StewardshipStep) -> None:
    if not donor.phone:
        return
    try:
        from ngo_homesuite.utils.sms_service import send_sms
        body = (step.body or "").replace("{name}", donor.name)
        send_sms(donor.phone, body)
    except Exception as exc:  # noqa: BLE001
        logger.error("SMS dispatch failed donor=%s: %s", donor.id, exc)
