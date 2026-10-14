import json
import hashlib
from datetime import date, datetime

import pytest

from ngo_homesuite.audit.security_events import SecurityAuditEvent, SecurityEventType
from ngo_homesuite.models.core import Donation, db
from ngo_homesuite.services.bank_reconciliation_service import BankReconciliationService
from ngo_homesuite.services.donation_service import (
    DonationService,
    _request_digest,
    next_manual_collection_date,
)
from ngo_homesuite.tests.test_financial_failure_correctness import (
    _create,
    financial_app,
)
from ngo_homesuite.tests.test_manual_recurring_and_currency import (
    _recurring_plan,
    _staff,
)

__all__ = ["financial_app"]


@pytest.mark.parametrize(
    "current,frequency,expected",
    [
        (date(2026, 1, 31), "monthly", date(2026, 2, 28)),
        (date(2024, 1, 31), "monthly", date(2024, 2, 29)),
        (date(2026, 11, 30), "quarterly", date(2027, 2, 28)),
        (date(2024, 2, 29), "yearly", date(2025, 2, 28)),
    ],
)
def test_calendar_month_end_and_leap_day(current, frequency, expected):
    assert next_manual_collection_date(current, frequency) == expected


def test_anchor_recovers_after_short_month_and_leap_year():
    assert next_manual_collection_date(
        date(2026, 2, 28), "monthly", anchor=date(2026, 1, 31)
    ) == date(2026, 3, 31)
    assert next_manual_collection_date(
        date(2027, 2, 28), "yearly", anchor=date(2024, 2, 29)
    ) == date(2028, 2, 29)


@pytest.mark.parametrize("frequency", ["weekly", "", "Monthly", "invalid"])
def test_calendar_frequency_never_silently_defaults(frequency):
    with pytest.raises(ValueError, match="frequency"):
        next_manual_collection_date(date(2026, 1, 1), frequency)


def test_schedule_is_date_only_and_not_affected_by_datetime_timezones():
    with pytest.raises(ValueError, match="calendar dates"):
        next_manual_collection_date(datetime(2026, 3, 8, 2, 30), "monthly")


def test_missed_run_preserves_occurrence_and_anchor_across_confirmation(financial_app):
    app = financial_app
    with app.app_context():
        plan = _recurring_plan(app)
        plan.next_charge_date = date(2026, 1, 31)
        db.session.commit()
        actor = _staff(app)
        svc = DonationService()
        assert (
            svc.schedule_due_manual_collections(
                app.config["TEST_ORG_ID"], run_date=date(2026, 4, 20)
            )["scheduled"]
            == 1
        )
        first = Donation.query.one()
        kwargs = {
            "actor_id": actor,
            "collection_reference": "external-row-1",
            "amount": 10.25,
            "currency": "USD",
        }
        svc.confirm_manual_collection(first.id, app.config["TEST_ORG_ID"], **kwargs)
        assert plan.next_charge_date == date(2026, 2, 28)
        svc.confirm_manual_collection(first.id, app.config["TEST_ORG_ID"], **kwargs)
        assert plan.next_charge_date == date(2026, 2, 28)
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 4, 20)
        )
        second = Donation.query.order_by(Donation.id.desc()).first()
        svc.confirm_manual_collection(
            second.id,
            app.config["TEST_ORG_ID"],
            **(kwargs | {"collection_reference": "external-row-2"}),
        )
        assert plan.next_charge_date == date(2026, 3, 31)


def test_new_audits_store_digest_not_sensitive_request_and_replay_still_compares(
    financial_app,
):
    app = financial_app
    sensitive = {
        "donor_email": "private-donor@example.org",
        "donor_phone": "+15555550123",
        "notes": "PRIVATE MEDICAL NOTE",
        "tribute_honoree_contact": "PRIVATE CONTACT",
        "tribute_honoree_name": "PRIVATE HONOREE",
    }
    with app.app_context():
        first = _create(app, "privacy-proof", **sensitive)
        audit = SecurityAuditEvent.query.one()
        rendered = json.dumps(audit.payload)
        assert len(audit.payload["request_hash"]) == 64
        assert len(audit.payload["request_hash_salt"]) == 64
        assert audit.payload["request_hash_version"] == 2
        for value in sensitive.values():
            assert value not in rendered
        assert _create(app, "privacy-proof", **sensitive).id == first.id
        with pytest.raises(ValueError, match="different request"):
            _create(app, "privacy-proof", **(sensitive | {"notes": "CHANGED"}))
        DonationService().update_donation(
            first.id, app.config["TEST_ORG_ID"], notes="NEW PRIVATE NOTE"
        )
        update = SecurityAuditEvent.query.order_by(SecurityAuditEvent.id.desc()).first()
        assert update.payload == {"changed_fields": ["notes"]}


def test_request_digest_canonicalization_domain_and_salt_separation():
    values = {"amount": 10.25, "notes": "private", "currency": "USD"}
    salt = "a1" * 32
    kwargs = {"version": 2, "organization_id": 1, "reference_number": "payment-1"}
    digest = _request_digest(values, salt, **kwargs)
    assert digest == _request_digest(dict(reversed(list(values.items()))), salt, **kwargs)
    assert digest != _request_digest(values, "b2" * 32, **kwargs)
    assert digest != _request_digest(values, salt, **(kwargs | {"organization_id": 2}))
    assert digest != _request_digest(values, salt, **(kwargs | {"reference_number": "payment-2"}))
    with pytest.raises(ValueError):
        _request_digest({"amount": float("nan")}, salt, **kwargs)


def test_version_one_digest_remains_compatible():
    values = {"amount": 10.25, "currency": "USD"}
    salt = "a1" * 16
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert _request_digest(values, salt) == hashlib.sha256((salt + ":" + canonical).encode()).hexdigest()


@pytest.mark.parametrize("field,value", [
    ("request_hash_version", 99), ("request_hash_version", True),
    ("request_hash_salt", ""), ("request_hash", "invalid"),
])
def test_corrupt_digest_metadata_never_succeeds_on_replay(financial_app, field, value):
    with financial_app.app_context():
        _create(financial_app, "corrupt-proof")
        event = SecurityAuditEvent.query.one()
        event.payload = event.payload | {field: value}
        db.session.commit()
        with pytest.raises(RuntimeError, match="idempotency audit metadata"):
            _create(financial_app, "corrupt-proof")


def test_financial_action_types_distinguish_lifecycle(financial_app):
    app = financial_app
    with app.app_context():
        svc = DonationService()
        donation = _create(app, "audit-types", status="pending")
        svc.finalize_payment(
            donation.id,
            app.config["TEST_ORG_ID"],
            "audit-types",
            donation.amount,
            "USD",
        )
        svc.update_status(donation.id, app.config["TEST_ORG_ID"], "processed")
        svc.generate_receipt(donation.id, app.config["TEST_ORG_ID"])
        svc.update_status(donation.id, app.config["TEST_ORG_ID"], "refunded")
        pending = _create(app, "delete-types", status="pending")
        svc.delete_donation(pending.id, app.config["TEST_ORG_ID"])
        pairs = [
            (row.action, row.event_type)
            for row in SecurityAuditEvent.query.order_by(SecurityAuditEvent.id)
        ]
        assert pairs == [
            ("donation.create", SecurityEventType.DONATION_CREATED),
            ("donation.payment_received", SecurityEventType.DONATION_PAYMENT_RECEIVED),
            ("donation.status", SecurityEventType.DONATION_STATUS_CHANGED),
            ("donation.receipt", SecurityEventType.DONATION_RECEIPT_GENERATED),
            ("donation.status", SecurityEventType.DONATION_REFUNDED),
            ("donation.create", SecurityEventType.DONATION_CREATED),
            ("donation.delete", SecurityEventType.DONATION_DELETED),
        ]


def test_reconciliation_audit_logs_counts_and_digests_not_raw_inputs(monkeypatch):
    logged = []
    monkeypatch.setattr(
        "ngo_homesuite.services.bank_reconciliation_service.log_event",
        lambda *args: logged.append(args),
    )
    entries = [
        {"tx_id": "PRIVATE BANK ID", "amount": 987.65, "source": "PRIVATE ACCOUNT"}
    ]
    result = BankReconciliationService().reconcile(
        entries, entries, actor="staff", organization_id=73
    )
    assert result["matched_count"] == 1
    metadata = logged[0][-1]
    assert metadata["organization_id"] == 73
    assert metadata["matched_count"] == 1
    assert len(metadata["reconciliation_id"]) == 32
    assert len(metadata["bank_digest"]) == 64
    for secret in ("PRIVATE BANK ID", "PRIVATE ACCOUNT", "987.65"):
        assert secret not in json.dumps(metadata)


def test_reconciliation_tenant_metadata_is_written_to_the_canonical_tenant_chain(financial_app):
    app = financial_app
    with app.app_context():
        BankReconciliationService().reconcile(
            [{"tx_id": "PRIVATE-ID", "amount": 10}],
            [{"tx_id": "PRIVATE-ID", "amount": 10}],
            organization_id=app.config["TEST_ORG_ID"],
        )
        audit = SecurityAuditEvent.query.one()
        assert audit.resource_org_id == app.config["TEST_ORG_ID"]
        assert audit.event_type == SecurityEventType.RECONCILIATION_COMPLETED
        assert audit.payload["matched_count"] == 1
        assert "PRIVATE-ID" not in json.dumps(audit.payload)
