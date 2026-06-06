"""Stewardship journey execution engine.

Processes active enrollments and dispatches due steps (email / SMS / wait).
Designed to be called from a cron job or scheduled Flask-APScheduler task.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from sqlalchemy import func, select
from ngo_homesuite.models.core import (
    Donation,
    DonorJourneyAutomationEvent,
    Donor,
    RecurringDonationPlan,
    StewardshipEnrollment,
    StewardshipJourney,
    StewardshipStep,
    Task,
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
        from ngo_homesuite.utils.email import send_email

        subject = (step.subject or "A message from us").replace("{name}", donor.name)
        body = (step.body or "").replace("{name}", donor.name).replace("{email}", donor.email or "")

        sent = send_email(to=donor.email, subject=subject, context={"text": body})
        if not sent:
            logger.warning(
                "Email delivery unavailable for stewardship step donor=%s email=%s",
                donor.id,
                donor.email,
            )
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


# ---------------------------------------------------------------------------
# Donor Journey Automations (Feature 3)
# ---------------------------------------------------------------------------

def _recent_automation_event(
    *,
    organization_id: int,
    trigger_name: str,
    donor_id: int | None,
    recurring_plan_id: int | None,
    since: datetime,
) -> Optional[DonorJourneyAutomationEvent]:
    query = select(DonorJourneyAutomationEvent).where(
        DonorJourneyAutomationEvent.organization_id == organization_id,
        DonorJourneyAutomationEvent.trigger_name == trigger_name,
        DonorJourneyAutomationEvent.status == "executed",
        DonorJourneyAutomationEvent.created_at >= since,
    )
    if donor_id is not None:
        query = query.where(DonorJourneyAutomationEvent.donor_id == int(donor_id))
    if recurring_plan_id is not None:
        query = query.where(DonorJourneyAutomationEvent.recurring_plan_id == int(recurring_plan_id))
    return db.session.scalars(
        query.order_by(DonorJourneyAutomationEvent.created_at.desc()).limit(1)
    ).first()


def _idempotency_exists(organization_id: int, idempotency_key: str) -> bool:
    count = db.session.scalar(
        select(func.count(DonorJourneyAutomationEvent.id)).where(
            DonorJourneyAutomationEvent.organization_id == organization_id,
            DonorJourneyAutomationEvent.idempotency_key == idempotency_key,
        )
    )
    return int(count or 0) > 0


def _record_automation_event(
    *,
    organization_id: int,
    trigger_name: str,
    action_type: str,
    status: str,
    idempotency_key: str,
    reason: str | None,
    cooldown_until: datetime | None,
    donor_id: int | None = None,
    recurring_plan_id: int | None = None,
    actor_user_id: int | None = None,
    related_task_id: int | None = None,
    related_enrollment_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> DonorJourneyAutomationEvent:
    event = DonorJourneyAutomationEvent(
        organization_id=int(organization_id),
        donor_id=int(donor_id) if donor_id is not None else None,
        recurring_plan_id=int(recurring_plan_id) if recurring_plan_id is not None else None,
        trigger_name=str(trigger_name),
        action_type=str(action_type),
        status=str(status),
        idempotency_key=str(idempotency_key),
        cooldown_until=cooldown_until,
        reason=(str(reason).strip() if reason else None),
        payload_json=payload or None,
        actor_user_id=int(actor_user_id) if actor_user_id is not None else None,
        related_task_id=int(related_task_id) if related_task_id is not None else None,
        related_enrollment_id=int(related_enrollment_id) if related_enrollment_id is not None else None,
    )
    db.session.add(event)
    return event


def _ensure_open_task(
    *,
    organization_id: int,
    donor_id: int,
    task_type: str,
    title: str,
    description: str,
    due_in_days: int,
) -> tuple[Task, bool]:
    existing = db.session.scalars(
        select(Task).where(
            Task.organization_id == organization_id,
            Task.donor_id == donor_id,
            Task.task_type == task_type,
            Task.status.in_(["open", "in_progress"]),
        ).order_by(Task.created_at.desc()).limit(1)
    ).first()
    if existing:
        return existing, False

    now = _utcnow()
    task = Task(
        organization_id=organization_id,
        donor_id=donor_id,
        title=title,
        description=description,
        task_type=task_type,
        priority="high",
        status="open",
        due_date=now + timedelta(days=max(0, int(due_in_days))),
        reminder_channel="email",
    )
    db.session.add(task)
    db.session.flush()
    return task, True


def _send_plain_email(donor: Donor, *, subject: str, body: str) -> tuple[bool, str | None]:
    if not donor.email:
        return False, "missing_email"
    try:
        from ngo_homesuite.utils.email import send_email

        sent = bool(
            send_email(
                to=donor.email,
                subject=subject.replace("{name}", str(donor.name or "friend")),
                context={
                    "text": body.replace("{name}", str(donor.name or "friend")),
                },
            )
        )
        return sent, None if sent else "delivery_unavailable"
    except Exception as exc:  # noqa: BLE001
        logger.warning("automation email failed donor=%s err=%s", donor.id, exc)
        return False, str(exc)


def run_donor_journey_automations(
    organization_id: int,
    *,
    actor_user_id: int | None = None,
    lapsing_days: int = 120,
    first_gift_window_days: int = 7,
    recurring_fail_threshold: int = 2,
    cooldown_days: int = 21,
) -> dict[str, Any]:
    """Run donor journey automations with idempotency, cooldowns, and audit trail."""
    now = _utcnow()
    cooldown_cutoff = now - timedelta(days=max(1, int(cooldown_days)))
    cooldown_until = now + timedelta(days=max(1, int(cooldown_days)))

    actions: list[dict[str, Any]] = []
    summary = {
        "evaluated": 0,
        "executed": 0,
        "skipped": 0,
        "failed": 0,
        "tasks_created": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "by_trigger": {
            "first_gift_followup": {"executed": 0, "skipped": 0, "failed": 0},
            "lapsing_donor_reactivation": {"executed": 0, "skipped": 0, "failed": 0},
            "recurring_failure_recovery": {"executed": 0, "skipped": 0, "failed": 0},
        },
    }

    def _mark(trigger: str, status: str) -> None:
        summary[status] = int(summary.get(status, 0)) + 1
        trigger_bucket = summary["by_trigger"][trigger]
        trigger_bucket[status] = int(trigger_bucket.get(status, 0)) + 1

    # Trigger 1: First gift follow-up (donor gave first gift recently).
    first_cutoff = now - timedelta(days=max(1, int(first_gift_window_days)))
    first_rows_stmt = (
        select(
            Donation.donor_id,
            func.count(Donation.id).label("gift_count"),
            func.max(Donation.donation_date).label("last_gift"),
            func.coalesce(func.sum(Donation.amount), 0.0).label("gift_total"),
        )
        .where(
            Donation.organization_id == organization_id,
            Donation.donor_id.is_not(None),
        )
        .group_by(Donation.donor_id)
        .having(func.count(Donation.id) == 1)
    )
    first_rows = db.session.execute(first_rows_stmt).all()
    for row in first_rows:
        donor_id = int(row.donor_id or 0)
        if donor_id <= 0:
            continue
        summary["evaluated"] = int(summary["evaluated"]) + 1
        last_gift = row.last_gift
        if last_gift is None or last_gift < first_cutoff:
            continue
        trigger = "first_gift_followup"
        idempotency_key = f"{trigger}:{donor_id}:{last_gift.date().isoformat()}"
        donor = db.session.get(Donor, donor_id)
        if donor is None or int(donor.organization_id or 0) != int(organization_id):
            continue
        if _idempotency_exists(organization_id, idempotency_key):
            _record_automation_event(
                organization_id=organization_id,
                donor_id=donor_id,
                trigger_name=trigger,
                action_type="task_and_email",
                status="skipped",
                idempotency_key=f"{idempotency_key}:repeat",
                reason="idempotency_hit",
                cooldown_until=cooldown_until,
                payload={"last_gift": last_gift.isoformat()},
                actor_user_id=actor_user_id,
            )
            _mark(trigger, "skipped")
            continue
        recent = _recent_automation_event(
            organization_id=organization_id,
            trigger_name=trigger,
            donor_id=donor_id,
            recurring_plan_id=None,
            since=cooldown_cutoff,
        )
        if recent is not None:
            _record_automation_event(
                organization_id=organization_id,
                donor_id=donor_id,
                trigger_name=trigger,
                action_type="task_and_email",
                status="skipped",
                idempotency_key=f"{idempotency_key}:cooldown",
                reason="cooldown_active",
                cooldown_until=recent.cooldown_until,
                payload={"recent_event_id": int(recent.id)},
                actor_user_id=actor_user_id,
            )
            _mark(trigger, "skipped")
            continue
        task, created = _ensure_open_task(
            organization_id=organization_id,
            donor_id=donor_id,
            task_type="first_gift_followup",
            title=f"First-gift follow-up: {donor.name}",
            description="Thank the donor, confirm impact preference, and invite second gift/recurring support.",
            due_in_days=2,
        )
        sent, err = _send_plain_email(
            donor,
            subject="Thank You For Your First Gift, {name}",
            body="Hi {name}, thank you for your first gift. We would love to share impact updates and hear your priorities.",
        )
        _record_automation_event(
            organization_id=organization_id,
            donor_id=donor_id,
            trigger_name=trigger,
            action_type="task_and_email",
            status="executed",
            idempotency_key=idempotency_key,
            reason=None if sent else (err or "email_not_sent"),
            cooldown_until=cooldown_until,
            related_task_id=int(task.id),
            payload={
                "gift_total": float(row.gift_total or 0.0),
                "last_gift": last_gift.isoformat(),
                "task_created": bool(created),
                "email_sent": bool(sent),
            },
            actor_user_id=actor_user_id,
        )
        if created:
            summary["tasks_created"] = int(summary["tasks_created"]) + 1
        if sent:
            summary["emails_sent"] = int(summary["emails_sent"]) + 1
        else:
            summary["emails_failed"] = int(summary["emails_failed"]) + 1
        _mark(trigger, "executed")
        actions.append({"trigger": trigger, "donor_id": donor_id, "task_id": int(task.id), "email_sent": bool(sent)})

    # Trigger 2: Lapsing donor reactivation (last gift stale and at least two total gifts).
    lapsing_cutoff = now - timedelta(days=max(30, int(lapsing_days)))
    lapsing_rows_stmt = (
        select(
            Donation.donor_id,
            func.count(Donation.id).label("gift_count"),
            func.max(Donation.donation_date).label("last_gift"),
            func.coalesce(func.sum(Donation.amount), 0.0).label("gift_total"),
        )
        .where(
            Donation.organization_id == organization_id,
            Donation.donor_id.is_not(None),
        )
        .group_by(Donation.donor_id)
        .having(func.count(Donation.id) >= 2)
    )
    lapsing_rows = db.session.execute(lapsing_rows_stmt).all()
    for row in lapsing_rows:
        donor_id = int(row.donor_id or 0)
        if donor_id <= 0:
            continue
        summary["evaluated"] = int(summary["evaluated"]) + 1
        last_gift = row.last_gift
        if last_gift is None or last_gift > lapsing_cutoff:
            continue
        trigger = "lapsing_donor_reactivation"
        idempotency_key = f"{trigger}:{donor_id}:{last_gift.date().isoformat()}"
        donor = db.session.get(Donor, donor_id)
        if donor is None or int(donor.organization_id or 0) != int(organization_id):
            continue
        if _idempotency_exists(organization_id, idempotency_key):
            _mark(trigger, "skipped")
            continue
        recent = _recent_automation_event(
            organization_id=organization_id,
            trigger_name=trigger,
            donor_id=donor_id,
            recurring_plan_id=None,
            since=cooldown_cutoff,
        )
        if recent is not None:
            _mark(trigger, "skipped")
            continue
        task, created = _ensure_open_task(
            organization_id=organization_id,
            donor_id=donor_id,
            task_type="lapsed_donor_reactivation",
            title=f"Reactivation plan: {donor.name}",
            description="Donor is lapsing. Prepare a personalized impact update and re-engagement ask.",
            due_in_days=3,
        )
        sent, err = _send_plain_email(
            donor,
            subject="We Miss You, {name}",
            body="Hi {name}, we wanted to share recent impact and reconnect. Your support has made a meaningful difference.",
        )
        _record_automation_event(
            organization_id=organization_id,
            donor_id=donor_id,
            trigger_name=trigger,
            action_type="task_and_email",
            status="executed",
            idempotency_key=idempotency_key,
            reason=None if sent else (err or "email_not_sent"),
            cooldown_until=cooldown_until,
            related_task_id=int(task.id),
            payload={
                "gift_count": int(row.gift_count or 0),
                "gift_total": float(row.gift_total or 0.0),
                "last_gift": last_gift.isoformat(),
                "task_created": bool(created),
                "email_sent": bool(sent),
            },
            actor_user_id=actor_user_id,
        )
        if created:
            summary["tasks_created"] = int(summary["tasks_created"]) + 1
        if sent:
            summary["emails_sent"] = int(summary["emails_sent"]) + 1
        else:
            summary["emails_failed"] = int(summary["emails_failed"]) + 1
        _mark(trigger, "executed")
        actions.append({"trigger": trigger, "donor_id": donor_id, "task_id": int(task.id), "email_sent": bool(sent)})

    # Trigger 3: Recurring failure recovery (failed/struggling recurring plan).
    recurring_rows_stmt = (
        select(RecurringDonationPlan)
        .where(
            RecurringDonationPlan.organization_id == organization_id,
            RecurringDonationPlan.donor_id.is_not(None),
            RecurringDonationPlan.status.in_(["failed", "paused", "active"]),
            RecurringDonationPlan.fail_count >= max(1, int(recurring_fail_threshold)),
        )
        .order_by(RecurringDonationPlan.fail_count.desc(), RecurringDonationPlan.updated_at.desc())
    )
    recurring_rows = db.session.execute(recurring_rows_stmt).scalars().all()
    for plan in recurring_rows:
        donor_id = int(plan.donor_id or 0)
        if donor_id <= 0:
            continue
        summary["evaluated"] = int(summary["evaluated"]) + 1
        trigger = "recurring_failure_recovery"
        plan_anchor = str(plan.updated_at.date().isoformat() if plan.updated_at else "na")
        idempotency_key = f"{trigger}:{int(plan.id)}:{int(plan.fail_count or 0)}:{plan_anchor}"
        donor = db.session.get(Donor, donor_id)
        if donor is None or int(donor.organization_id or 0) != int(organization_id):
            continue
        if _idempotency_exists(organization_id, idempotency_key):
            _mark(trigger, "skipped")
            continue
        recent = _recent_automation_event(
            organization_id=organization_id,
            trigger_name=trigger,
            donor_id=donor_id,
            recurring_plan_id=int(plan.id),
            since=cooldown_cutoff,
        )
        if recent is not None:
            _mark(trigger, "skipped")
            continue
        task, created = _ensure_open_task(
            organization_id=organization_id,
            donor_id=donor_id,
            task_type="recurring_failure_recovery",
            title=f"Recurring recovery: {donor.name}",
            description="Recurring gift plan has repeated failures. Reach out to update payment details and retain support.",
            due_in_days=1,
        )
        sent, err = _send_plain_email(
            donor,
            subject="Action Needed To Resume Your Recurring Gift, {name}",
            body="Hi {name}, we had trouble processing your recurring gift. Please update payment details so support continues uninterrupted.",
        )
        _record_automation_event(
            organization_id=organization_id,
            donor_id=donor_id,
            recurring_plan_id=int(plan.id),
            trigger_name=trigger,
            action_type="task_and_email",
            status="executed",
            idempotency_key=idempotency_key,
            reason=None if sent else (err or "email_not_sent"),
            cooldown_until=cooldown_until,
            related_task_id=int(task.id),
            payload={
                "fail_count": int(plan.fail_count or 0),
                "plan_status": str(plan.status or ""),
                "last_error": str(plan.last_error or "") or None,
                "task_created": bool(created),
                "email_sent": bool(sent),
            },
            actor_user_id=actor_user_id,
        )
        if created:
            summary["tasks_created"] = int(summary["tasks_created"]) + 1
        if sent:
            summary["emails_sent"] = int(summary["emails_sent"]) + 1
        else:
            summary["emails_failed"] = int(summary["emails_failed"]) + 1
        _mark(trigger, "executed")
        actions.append({"trigger": trigger, "donor_id": donor_id, "plan_id": int(plan.id), "task_id": int(task.id), "email_sent": bool(sent)})

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "summary": summary,
        "actions": actions,
        "parameters": {
            "lapsing_days": int(lapsing_days),
            "first_gift_window_days": int(first_gift_window_days),
            "recurring_fail_threshold": int(recurring_fail_threshold),
            "cooldown_days": int(cooldown_days),
        },
        "generated_at": now.isoformat(timespec="seconds"),
    }


def list_donor_journey_automation_events(
    organization_id: int,
    *,
    trigger_name: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 500))
    query = select(DonorJourneyAutomationEvent).where(
        DonorJourneyAutomationEvent.organization_id == organization_id,
    )
    if trigger_name:
        query = query.where(DonorJourneyAutomationEvent.trigger_name == str(trigger_name).strip())
    if status:
        query = query.where(DonorJourneyAutomationEvent.status == str(status).strip())

    rows = db.session.scalars(
        query.order_by(DonorJourneyAutomationEvent.created_at.desc()).limit(safe_limit)
    ).all()
    return [
        {
            "id": int(item.id),
            "organization_id": int(item.organization_id),
            "donor_id": int(item.donor_id) if item.donor_id is not None else None,
            "recurring_plan_id": int(item.recurring_plan_id) if item.recurring_plan_id is not None else None,
            "trigger_name": str(item.trigger_name),
            "action_type": str(item.action_type),
            "status": str(item.status),
            "idempotency_key": str(item.idempotency_key),
            "cooldown_until": item.cooldown_until.isoformat() if item.cooldown_until else None,
            "reason": item.reason,
            "payload": item.payload_json,
            "actor_user_id": int(item.actor_user_id) if item.actor_user_id is not None else None,
            "related_task_id": int(item.related_task_id) if item.related_task_id is not None else None,
            "related_enrollment_id": int(item.related_enrollment_id) if item.related_enrollment_id is not None else None,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in rows
    ]


def get_stewardship_journey_graph(organization_id: int, journey_id: int) -> dict[str, Any] | None:
    journey = db.session.scalars(
        select(StewardshipJourney).where(
            StewardshipJourney.id == int(journey_id),
            StewardshipJourney.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if journey is None:
        return None

    steps = list(journey.steps or [])
    nodes = [
        {
            "id": int(step.id),
            "journey_id": int(journey.id),
            "step_order": int(step.step_order or 0),
            "step_type": str(step.step_type or "wait"),
            "delay_days": int(step.delay_days or 0),
            "template_name": str(step.template_name or "") or None,
            "subject": str(step.subject or "") or None,
            "body": str(step.body or "") or None,
        }
        for step in steps
    ]
    edges = []
    for index, step in enumerate(steps[:-1]):
        next_step = steps[index + 1]
        edges.append(
            {
                "from_step_id": int(step.id),
                "to_step_id": int(next_step.id),
                "transition": "next",
                "delay_days": int(next_step.delay_days or 0),
            }
        )

    return {
        "journey": {
            "id": int(journey.id),
            "organization_id": int(journey.organization_id),
            "name": str(journey.name or ""),
            "trigger": str(journey.trigger or ""),
            "is_active": bool(journey.is_active),
        },
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }


def list_stewardship_journey_executions(
    organization_id: int,
    journey_id: int,
    *,
    limit: int = 50,
) -> dict[str, Any] | None:
    journey = db.session.scalars(
        select(StewardshipJourney).where(
            StewardshipJourney.id == int(journey_id),
            StewardshipJourney.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if journey is None:
        return None

    safe_limit = max(1, min(int(limit), 200))
    steps = list(journey.steps or [])
    enrollments = db.session.scalars(
        select(StewardshipEnrollment).where(
            StewardshipEnrollment.organization_id == int(organization_id),
            StewardshipEnrollment.journey_id == int(journey_id),
        ).order_by(StewardshipEnrollment.enrolled_at.desc()).limit(safe_limit)
    ).all()

    items = []
    for enrollment in enrollments:
        current_step_index = int(enrollment.current_step or 0)
        step_states = []
        for index, step in enumerate(steps):
            if str(enrollment.status or "") == "completed":
                state = "completed"
            elif index < current_step_index:
                state = "completed"
            elif index == current_step_index and str(enrollment.status or "") == "active":
                state = "current"
            else:
                state = "pending"
            step_states.append(
                {
                    "step_id": int(step.id),
                    "step_order": int(step.step_order or 0),
                    "step_type": str(step.step_type or "wait"),
                    "state": state,
                    "delay_days": int(step.delay_days or 0),
                }
            )

        items.append(
            {
                "enrollment_id": int(enrollment.id),
                "donor_id": int(enrollment.donor_id),
                "donor_name": str(getattr(enrollment.donor, "name", "") or ""),
                "status": str(enrollment.status or "active"),
                "current_step": current_step_index,
                "enrolled_at": enrollment.enrolled_at.isoformat() if enrollment.enrolled_at else None,
                "next_step_due": enrollment.next_step_due.isoformat() if enrollment.next_step_due else None,
                "completed_at": enrollment.completed_at.isoformat() if enrollment.completed_at else None,
                "steps": step_states,
            }
        )

    return {
        "journey": {
            "id": int(journey.id),
            "organization_id": int(journey.organization_id),
            "name": str(journey.name or ""),
            "trigger": str(journey.trigger or ""),
        },
        "count": len(items),
        "executions": items,
    }


