from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ngo_homesuite.models.core import Donation, Donor, FormSubmissionEvent, Task, db
from ngo_homesuite.services.donor_service import DonorService


_ALLOWED_FORM_TYPES = {
    "donation",
    "volunteer",
    "contact",
    "membership",
    "event_registration",
    "case_intake",
    "general",
}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_email(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    return raw or None


def _normalize_phone(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw or None


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("submitted_at must be ISO datetime")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class FormEcosystemService:
    @staticmethod
    def _build_idempotency_key(
        *,
        source: str,
        form_type: str,
        external_submission_id: str | None,
        payload: dict[str, Any],
    ) -> str:
        if external_submission_id:
            return f"{source}:{external_submission_id.strip().lower()}"

        canonical = {
            "source": source,
            "form_type": form_type,
            "submitter_email": _normalize_email(payload.get("submitter_email") or payload.get("email")),
            "submitter_name": str(payload.get("submitter_name") or payload.get("name") or "").strip().lower(),
            "submitter_phone": _normalize_phone(payload.get("submitter_phone") or payload.get("phone")),
            "amount": float(payload.get("amount") or 0.0),
            "currency": str(payload.get("currency") or "USD").strip().upper(),
            "message": str(payload.get("message") or "").strip().lower(),
            "submitted_at": str(payload.get("submitted_at") or "").strip(),
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return f"{source}:hash:{digest}"

    @staticmethod
    def _ensure_donor(
        org_id: int,
        *,
        submitter_name: str,
        submitter_email: str | None,
        submitter_phone: str | None,
        source: str,
    ) -> tuple[Donor, bool]:
        donor_service = DonorService()
        donor, donor_created = donor_service.find_or_create_by_email(
            org_id,
            submitter_email,
            submitter_name,
            phone=submitter_phone,
            source=source,
            status="active",
        )

        changed = False
        if submitter_phone and not donor.phone:
            donor.phone = submitter_phone
            changed = True
        if source:
            existing_sources = {s.strip().lower() for s in str(donor.source or "").split(",") if s.strip()}
            if source.lower() not in existing_sources:
                merged = ", ".join(sorted(existing_sources | {source.lower()}))
                donor.source = merged
                changed = True
        if changed:
            db.session.flush()
        return donor, donor_created

    @staticmethod
    def submit_form(
        *,
        org_id: int,
        source: str,
        form_type: str,
        payload: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        source_norm = str(source or "").strip().lower()
        if not source_norm:
            raise ValueError("source is required")

        form_type_norm = str(form_type or "").strip().lower()
        if form_type_norm not in _ALLOWED_FORM_TYPES:
            allowed = ", ".join(sorted(_ALLOWED_FORM_TYPES))
            raise ValueError(f"form_type must be one of: {allowed}")

        external_submission_id = str(payload.get("external_submission_id") or "").strip() or None
        idempotency_key = FormEcosystemService._build_idempotency_key(
            source=source_norm,
            form_type=form_type_norm,
            external_submission_id=external_submission_id,
            payload=payload,
        )

        existing = db.session.scalar(
            select(FormSubmissionEvent).where(
                FormSubmissionEvent.organization_id == int(org_id),
                FormSubmissionEvent.idempotency_key == idempotency_key,
            ).limit(1)
        )
        if existing is not None:
            return {
                "duplicate": True,
                "submission_id": int(existing.id),
                "idempotency_key": existing.idempotency_key,
                "source": existing.source,
                "form_type": existing.form_type,
                "donor_id": int(existing.donor_id) if existing.donor_id else None,
                "donation_id": int(existing.donation_id) if existing.donation_id else None,
                "task_id": int(existing.task_id) if existing.task_id else None,
                "status": existing.status,
            }

        submitter_name = str(
            payload.get("submitter_name")
            or payload.get("name")
            or payload.get("donor_name")
            or "Anonymous Supporter"
        ).strip() or "Anonymous Supporter"
        submitter_email = _normalize_email(payload.get("submitter_email") or payload.get("email") or payload.get("donor_email"))
        submitter_phone = _normalize_phone(payload.get("submitter_phone") or payload.get("phone") or payload.get("donor_phone"))
        message = str(payload.get("message") or payload.get("notes") or "").strip() or None

        submitted_at = _parse_iso_datetime(payload.get("submitted_at")) or _utcnow_naive()
        form_name = str(payload.get("form_name") or "").strip() or None

        amount_raw = payload.get("amount")
        amount: float | None = None
        if amount_raw not in (None, ""):
            try:
                amount = float(amount_raw)
            except (TypeError, ValueError):
                raise ValueError("amount must be numeric")
            if amount <= 0:
                raise ValueError("amount must be greater than 0")

        currency = str(payload.get("currency") or "USD").strip().upper() or "USD"
        if len(currency) != 3:
            raise ValueError("currency must be a 3-letter code")

        donor, donor_created = FormEcosystemService._ensure_donor(
            int(org_id),
            submitter_name=submitter_name,
            submitter_email=submitter_email,
            submitter_phone=submitter_phone,
            source=source_norm,
        )

        donation = None
        should_create_donation = form_type_norm in {"donation", "membership", "event_registration"} and amount is not None
        if should_create_donation:
            reference_number = str(payload.get("reference_number") or "").strip() or None
            if not reference_number:
                reference_number = f"FORM-{idempotency_key[:40].upper()}"
            donation = Donation(
                organization_id=int(org_id),
                donor_id=int(donor.id),
                donor_name=str(donor.name),
                donor_email=donor.email,
                donor_phone=donor.phone,
                amount=float(amount),
                currency=currency,
                payment_method=str(payload.get("payment_method") or "form").strip() or "form",
                channel=str(payload.get("channel") or "web_form").strip() or "web_form",
                reference_number=reference_number,
                purpose=str(payload.get("purpose") or "").strip() or None,
                status="received",
                notes=message,
                donation_date=submitted_at,
            )
            db.session.add(donation)
            db.session.flush()

        create_followup = bool(payload.get("create_followup_task", form_type_norm in {"contact", "volunteer", "case_intake"}))
        if bool(payload.get("follow_up_requested", False)):
            create_followup = True

        task = None
        if create_followup:
            title_prefix = {
                "contact": "Respond to contact form",
                "volunteer": "Review volunteer application",
                "case_intake": "Triage case intake",
                "donation": "Steward donor submission",
                "membership": "Welcome new membership",
                "event_registration": "Confirm event registration",
                "general": "Review form submission",
            }.get(form_type_norm, "Review form submission")
            due_days = 2 if form_type_norm == "case_intake" else 5
            task = Task(
                organization_id=int(org_id),
                donor_id=int(donor.id),
                donation_id=int(donation.id) if donation is not None else None,
                title=f"{title_prefix}: {submitter_name}",
                description=(
                    f"Source={source_norm}; form={form_name or form_type_norm}; "
                    f"submission_id={external_submission_id or 'n/a'}; message={message or 'n/a'}"
                ),
                task_type="follow_up",
                priority="high" if form_type_norm in {"case_intake", "volunteer"} else "medium",
                status="open",
                due_date=_utcnow_naive() + timedelta(days=due_days),
                reminder_channel="email",
            )
            db.session.add(task)
            db.session.flush()

        event = FormSubmissionEvent(
            organization_id=int(org_id),
            source=source_norm,
            form_name=form_name,
            form_type=form_type_norm,
            external_submission_id=external_submission_id,
            idempotency_key=idempotency_key,
            submitter_name=submitter_name,
            submitter_email=submitter_email,
            submitter_phone=submitter_phone,
            donor_id=int(donor.id),
            donation_id=int(donation.id) if donation is not None else None,
            task_id=int(task.id) if task is not None else None,
            amount=amount,
            currency=currency if amount is not None else None,
            message=message,
            metadata_json={
                "source": source_norm,
                "form_name": form_name,
                "form_type": form_type_norm,
                "donor_created": donor_created,
            },
            raw_payload_json=payload,
            submitted_at=submitted_at,
            processed_at=_utcnow_naive(),
            status="processed",
            actor_user_id=int(actor_user_id) if actor_user_id else None,
        )
        db.session.add(event)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = db.session.scalar(
                select(FormSubmissionEvent).where(
                    FormSubmissionEvent.organization_id == int(org_id),
                    FormSubmissionEvent.idempotency_key == idempotency_key,
                ).limit(1)
            )
            if existing is not None:
                return {
                    "duplicate": True,
                    "submission_id": int(existing.id),
                    "idempotency_key": existing.idempotency_key,
                    "source": existing.source,
                    "form_type": existing.form_type,
                    "donor_id": int(existing.donor_id) if existing.donor_id else None,
                    "donation_id": int(existing.donation_id) if existing.donation_id else None,
                    "task_id": int(existing.task_id) if existing.task_id else None,
                    "status": existing.status,
                }
            raise

        return {
            "duplicate": False,
            "submission_id": int(event.id),
            "idempotency_key": event.idempotency_key,
            "source": event.source,
            "form_type": event.form_type,
            "donor_id": int(event.donor_id) if event.donor_id else None,
            "donor_created": donor_created,
            "donation_id": int(event.donation_id) if event.donation_id else None,
            "task_id": int(event.task_id) if event.task_id else None,
            "status": event.status,
            "processed_at": event.processed_at.isoformat() if event.processed_at else None,
        }

    @staticmethod
    def list_submissions(
        *,
        org_id: int,
        source: str | None = None,
        form_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = select(FormSubmissionEvent).where(FormSubmissionEvent.organization_id == int(org_id))
        if source:
            stmt = stmt.where(FormSubmissionEvent.source == str(source).strip().lower())
        if form_type:
            stmt = stmt.where(FormSubmissionEvent.form_type == str(form_type).strip().lower())
        if status:
            stmt = stmt.where(FormSubmissionEvent.status == str(status).strip().lower())

        capped_limit = max(1, min(int(limit), 500))
        rows = list(
            db.session.scalars(
                stmt.order_by(FormSubmissionEvent.submitted_at.desc(), FormSubmissionEvent.id.desc()).limit(capped_limit)
            )
        )

        return [
            {
                "id": int(row.id),
                "source": row.source,
                "form_name": row.form_name,
                "form_type": row.form_type,
                "external_submission_id": row.external_submission_id,
                "submitter_name": row.submitter_name,
                "submitter_email": row.submitter_email,
                "submitter_phone": row.submitter_phone,
                "donor_id": int(row.donor_id) if row.donor_id else None,
                "donation_id": int(row.donation_id) if row.donation_id else None,
                "task_id": int(row.task_id) if row.task_id else None,
                "amount": float(row.amount) if row.amount is not None else None,
                "currency": row.currency,
                "status": row.status,
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                "processed_at": row.processed_at.isoformat() if row.processed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
