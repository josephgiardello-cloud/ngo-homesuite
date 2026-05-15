from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import logging

from ngo_homesuite.db.audit_log import log_event
from ngo_homesuite.models.core import Donation, DonationReceipt, db


logger = logging.getLogger(__name__)


def _safe_log_event(*, db_path: str, actor: str, action: str, entity: str, metadata: dict) -> None:
    try:
        log_event(db_path=db_path, actor=actor, action=action, entity=entity, metadata=metadata)
    except Exception as exc:  # pragma: no cover
        logger.warning('workflow_audit_log_failed', extra={'action': action, 'error': str(exc)})


def _make_receipt_number(donation_id: int) -> str:
    return f"R-{donation_id:06d}-{datetime.utcnow().strftime('%Y%m%d')}"


def run_donation_receipt_followup_workflow(*, donation_id: int, actor: str, db_path: str = 'ngo_homesuite.db') -> dict:
    donation = Donation.query.filter_by(id=donation_id).first()
    if donation is None:
        return {
            "ok": False,
            "workflow": "donation_receipt_followup",
            "error": f"Donation {donation_id} not found.",
        }

    receipt = DonationReceipt.query.filter_by(donation_id=donation.id).first()
    created_receipt = False
    if receipt is None:
        receipt = DonationReceipt(
            donation_id=donation.id,
            receipt_number=_make_receipt_number(donation.id),
            status="generated",
            sent_to_email=donation.donor_email,
        )
        db.session.add(receipt)
        donation.status = "receipted"
        db.session.commit()
        created_receipt = True

    follow_up = {
        "channel": "email" if donation.donor_email else "phone",
        "target": donation.donor_email or donation.donor_phone,
        "message": f"Thank you {donation.donor_name} for your contribution of {donation.amount} {donation.currency}.",
    }

    _safe_log_event(
        db_path=db_path,
        actor=actor,
        action="workflow_donation_receipt_followup",
        entity="workflow",
        metadata={
            "donation_id": donation.id,
            "receipt_created": created_receipt,
            "follow_up_channel": follow_up["channel"],
        },
    )

    return {
        "ok": True,
        "workflow": "donation_receipt_followup",
        "donation_id": donation.id,
        "receipt_number": receipt.receipt_number,
        "receipt_created": created_receipt,
        "follow_up": follow_up,
    }


def run_grant_tracking_reporting_workflow(*, grant_name: str, requested_amount: float, actor: str, db_path: str = 'ngo_homesuite.db') -> dict:
    milestones = [
        {"stage": "application_submitted", "status": "completed"},
        {"stage": "review", "status": "in_progress"},
        {"stage": "reporting_due", "status": "pending"},
    ]
    summary = {
        "grant_name": grant_name,
        "requested_amount": requested_amount,
        "pipeline_status": "review",
        "milestones": milestones,
    }

    _safe_log_event(
        db_path=db_path,
        actor=actor,
        action="workflow_grant_tracking_reporting",
        entity="workflow",
        metadata=summary,
    )

    return {
        "ok": True,
        "workflow": "grant_tracking_reporting",
        "summary": summary,
    }


def run_program_tracking_impact_workflow(*, program_name: str, beneficiary_count: int, outcomes: list[dict], actor: str, db_path: str = 'ngo_homesuite.db') -> dict:
    impact_score = 0.0
    if outcomes:
        impact_score = round(sum(float(item.get("metric_value", 0.0)) for item in outcomes) / len(outcomes), 2)

    report = {
        "program_name": program_name,
        "beneficiary_count": beneficiary_count,
        "outcomes": outcomes,
        "impact_score": impact_score,
        "generated_at": datetime.utcnow().isoformat(),
    }

    _safe_log_event(
        db_path=db_path,
        actor=actor,
        action="workflow_program_tracking_impact",
        entity="workflow",
        metadata=report,
    )

    return {
        "ok": True,
        "workflow": "program_tracking_impact_report",
        "report": report,
    }
