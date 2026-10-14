from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from itertools import permutations
from pathlib import Path
from threading import Barrier

import pytest
from flask import Flask
from flask_login import LoginManager
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from ngo_homesuite.audit.security_events import SecurityAuditEvent, SecurityAuditService
from ngo_homesuite.models.core import (
    Donation,
    DonationReceipt,
    Donor,
    Fund,
    Organization,
    RecurringDonationPlan,
    User,
    db,
)
from ngo_homesuite.services.bank_reconciliation_service import BankReconciliationService
from ngo_homesuite.services.donation_service import (
    DonationConcurrencyError,
    DonationService,
)
from ngo_homesuite.services.fund_service import FundService
from ngo_homesuite.services.payment_service import (
    PaymentService,
    WebhookProcessingError,
)


@pytest.fixture
def financial_app(tmp_path, monkeypatch):
    """Real ORM/services on a file DB; no demo seeds, HTTP mocks, or shared connections."""
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'financial.sqlite3'}",
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 20}},
    )
    db.init_app(app)
    monkeypatch.setattr(
        "ngo_homesuite.services.payment_service._send_receipt_email", lambda _: None
    )
    monkeypatch.setattr(
        "ngo_homesuite.services.bank_reconciliation_service.log_event",
        lambda *a, **kw: None,
    )
    with app.app_context():

        @event.listens_for(db.engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        db.create_all()
        org = Organization(
            name="Financial verification", slug="financial-verification", is_active=True
        )
        db.session.add(org)
        db.session.flush()
        fund = Fund(organization_id=org.id, name="Verified fund")
        db.session.add(fund)
        db.session.commit()
        app.config["TEST_ORG_ID"] = org.id
        app.config["TEST_FUND_ID"] = fund.id
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _create(app, reference, amount="12.34", **kwargs):
    return DonationService().create_donation(
        app.config["TEST_ORG_ID"],
        "Verified donor",
        float(amount),
        fund_id=app.config["TEST_FUND_ID"],
        reference_number=reference,
        **kwargs,
    )


def _ledger(app):
    donations = db.session.scalars(select(Donation).order_by(Donation.id)).all()
    audits = db.session.scalars(
        select(SecurityAuditEvent).where(SecurityAuditEvent.action == "donation.create")
    ).all()
    assert len(audits) == len(donations)
    assert {a.resource_id for a in audits} == {d.id for d in donations}
    assert all(a.resource_org_id == app.config["TEST_ORG_ID"] for a in audits)
    assert SecurityAuditService.verify_chain_integrity(app.config["TEST_ORG_ID"])
    assert all(
        Decimal(str(a.payload["request"]["amount"])) == Decimal(str(d.amount))
        for a in audits
        for d in donations
        if a.resource_id == d.id
    )
    return [{"tx_id": d.reference_number, "amount": d.amount} for d in donations]


def test_simultaneous_distinct_donations_reconcile_exactly(financial_app):
    app = financial_app
    workers = 8
    gate = Barrier(workers)

    def submit(index):
        with app.app_context():
            gate.wait(timeout=10)
            return _create(app, f"concurrent-{index}", f"{index + 1}.01").id

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ids = list(pool.map(submit, range(workers)))
    assert len(set(ids)) == workers
    with app.app_context():
        ledger = _ledger(app)
        expected = sum((Decimal(f"{i + 1}.01") for i in range(workers)), Decimal(0))
        assert (
            sum((Decimal(str(row["amount"])) for row in ledger), Decimal(0)) == expected
        )
        balance = FundService().get_fund_balance(
            app.config["TEST_FUND_ID"], app.config["TEST_ORG_ID"]
        )
        assert Decimal(str(balance["total_in"])) == expected
        result = BankReconciliationService().reconcile(list(reversed(ledger)), ledger)
        assert result["status"] == "balanced"
        assert result["matched_count"] == workers
        assert Decimal(str(result["matched_total"])) == expected


def test_simultaneous_duplicate_submissions_make_one_record_and_audit(financial_app):
    app = financial_app
    gate = Barrier(8)

    def submit(_):
        with app.app_context():
            gate.wait(timeout=10)
            return _create(app, "same-payment").id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(submit, range(8)))
    assert len(set(ids)) == 1
    with app.app_context():
        assert _ledger(app) == [{"tx_id": "same-payment", "amount": 12.34}]


@pytest.mark.parametrize(
    "stage", ["after_donation_insert", "audit_insert", "before_commit"]
)
def test_partial_failure_rolls_back_and_retry_is_exactly_once(financial_app, stage):
    app = financial_app
    with app.app_context():
        engine = db.engine

        def fail_statement(_conn, _cursor, statement, _parameters, _context, _many):
            if "INSERT INTO security_audit_events" in statement:
                raise OperationalError(
                    statement, {}, RuntimeError("injected audit failure")
                )

        def fail_after_insert(_mapper, _connection, _target):
            raise OperationalError(
                "after donation insert", {}, RuntimeError("injected failure")
            )

        def fail_commit(_session):
            raise OperationalError(
                "before commit", {}, RuntimeError("injected failure")
            )

        target, hook, callback = {
            "after_donation_insert": (Donation, "after_insert", fail_after_insert),
            "audit_insert": (engine, "before_cursor_execute", fail_statement),
            "before_commit": (db.session(), "before_commit", fail_commit),
        }[stage]
        event.listen(target, hook, callback)
        try:
            with pytest.raises(OperationalError, match="injected"):
                _create(app, "failed-then-retried")
        finally:
            event.remove(target, hook, callback)
        # The service must leave the session usable, not require callers to repair it.
        assert db.session.scalar(select(Donation.id)) is None
        assert db.session.scalar(select(SecurityAuditEvent.id)) is None
        first = _create(app, "failed-then-retried")
        second = _create(app, "failed-then-retried")
        assert first.id == second.id
        assert len(_ledger(app)) == 1


def test_changed_duplicate_payload_is_not_silent_success(financial_app):
    with financial_app.app_context():
        _create(financial_app, "immutable-reference", "15")
        with pytest.raises(ValueError, match="reference"):
            _create(financial_app, "immutable-reference", "16")
        assert _ledger(financial_app) == [
            {"tx_id": "immutable-reference", "amount": 15.0}
        ]


@pytest.mark.parametrize("amount", ["nan", "inf", "-inf", "0.001"])
def test_invalid_money_creates_neither_financial_record_nor_audit(
    financial_app, amount
):
    with financial_app.app_context():
        with pytest.raises(ValueError):
            _create(financial_app, "invalid", amount)
        assert _ledger(financial_app) == []


def test_pending_failed_and_refunded_donations_are_not_available_funds(financial_app):
    with financial_app.app_context():
        for status in ("pending", "failed", "refunded"):
            _create(financial_app, status, "100", status=status)
        for status in ("received", "processed", "receipted"):
            _create(financial_app, status, "10", status=status)
        balance = FundService().get_fund_balance(
            financial_app.config["TEST_FUND_ID"],
            financial_app.config["TEST_ORG_ID"],
        )
        assert balance["total_in"] == 30.0


def test_reconciliation_does_not_hide_conflicting_transaction_ids(financial_app):
    with financial_app.app_context():
        result = BankReconciliationService().reconcile(
            [{"tx_id": "bank-payment", "amount": "25"}],
            [{"tx_id": "different-payment", "amount": "25"}],
        )
        assert result["status"] == "mismatch"
        assert result["matched_count"] == 0


def test_reconciliation_amount_fallback_only_when_identity_is_missing(financial_app):
    with financial_app.app_context():
        result = BankReconciliationService().reconcile(
            [{"tx_id": "bank-payment", "amount": "25"}, {"amount": "25"}],
            [{"amount": "25"}],
        )
        assert result["status"] == "mismatch"
        assert result["matched_count"] == 1
        assert result["unmatched_bank_count"] == 1


def test_reconciliation_detects_duplicate_and_missing_ledger_records(financial_app):
    with financial_app.app_context():
        _create(financial_app, "one", "12")
        _create(financial_app, "two", "13")
        ledger = [
            {"tx_id": d.reference_number, "amount": d.amount}
            for d in db.session.scalars(select(Donation))
        ]
        bank = [{"tx_id": "one", "amount": "12"}, {"tx_id": "two", "amount": "13"}]
        svc = BankReconciliationService()
        assert svc.reconcile(bank, ledger + [ledger[0]])["unmatched_ledger_count"] == 1
        assert svc.reconcile(bank, ledger[:1])["unmatched_bank_count"] == 1


def test_duplicate_webhook_cannot_resurrect_refunded_donation(financial_app):
    app = financial_app
    with app.app_context():
        pending = _create(app, None, "12.34", status="pending")
        payload = {
            "id": "checkout-verified",
            "payment_intent": "intent-verified",
            "payment_status": "paid",
            "amount_total": 1234,
            "currency": "usd",
            "metadata": {
                "org_id": str(app.config["TEST_ORG_ID"]),
                "donation_id": str(pending.id),
            },
        }
        service = PaymentService()
        received = service.handle_checkout_completed(payload)
        DonationService().update_status(
            received.id, app.config["TEST_ORG_ID"], "refunded"
        )
        replay = service.handle_checkout_completed(payload)
        assert replay.status == "refunded"
        assert Donation.query.count() == 1


def test_webhook_must_match_pending_amount(financial_app):
    app = financial_app
    with app.app_context():
        pending = _create(app, None, "20", status="pending")
        with pytest.raises(WebhookProcessingError, match="amount"):
            PaymentService().handle_checkout_completed(
                {
                    "id": "checkout-wrong",
                    "payment_status": "paid",
                    "amount_total": 100,
                    "currency": "usd",
                    "metadata": {
                        "org_id": str(app.config["TEST_ORG_ID"]),
                        "donation_id": str(pending.id),
                    },
                }
            )
        db.session.refresh(pending)
        assert pending.status == "pending"


def _crash_worker(uri, org_id, fund_id, stage):
    import os

    app = Flask("crash-worker")
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    db.init_app(app)
    with app.app_context():

        def crash(*_args):
            os._exit(73)

        target, hook = {
            "after_donation_insert": (Donation, "after_insert"),
            "after_audit_insert": (SecurityAuditEvent, "after_insert"),
            "before_commit": (db.session(), "before_commit"),
            "after_commit": (db.session(), "after_commit"),
        }[stage]
        event.listen(target, hook, crash)
        DonationService().create_donation(
            int(org_id),
            "Verified donor",
            12.34,
            fund_id=int(fund_id),
            reference_number="crash-payment",
        )
        raise AssertionError("Crash injection was not reached")


@pytest.mark.parametrize(
    "stage",
    [
        "after_donation_insert",
        "after_audit_insert",
        "before_commit",
        "after_commit",
    ],
)
def test_process_death_and_lost_response_retry(financial_app, stage):
    app = financial_app
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from ngo_homesuite.tests.test_financial_failure_correctness import _crash_worker; "
                "import sys; _crash_worker(*sys.argv[1:])"
            ),
            app.config["SQLALCHEMY_DATABASE_URI"],
            str(app.config["TEST_ORG_ID"]),
            str(app.config["TEST_FUND_ID"]),
            stage,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 73, result.stderr
    with app.app_context():
        # Read from a new session after a process died without rollback/teardown.
        db.session.remove()
        ledger = _ledger(app)
        assert len(ledger) == (1 if stage == "after_commit" else 0)
        first = _create(app, "crash-payment")
        assert _create(app, "crash-payment").id == first.id
        assert _ledger(app) == [{"tx_id": "crash-payment", "amount": 12.34}]


def test_conflicting_concurrent_submissions_cannot_change_the_winner(financial_app):
    app = financial_app
    gate = Barrier(2)

    def submit(amount):
        with app.app_context():
            gate.wait(timeout=10)
            try:
                return ("accepted", _create(app, "conflicting-payment", amount).amount)
            except ValueError as exc:
                assert "reference" in str(exc)
                return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ("10", "20")))
    assert sorted(result[0] for result in results) == ["accepted", "conflict"]
    with app.app_context():
        assert _ledger(app)[0]["amount"] == next(
            amount for status, amount in results if status == "accepted"
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"currency": "EUR"},
        {"donor_email": "different@example.org"},
        {"notes": "changed"},
    ],
)
def test_duplicate_identity_compares_request_not_just_amount(financial_app, changed):
    with financial_app.app_context():
        _create(financial_app, "immutable-request")
        with pytest.raises(ValueError, match="reference"):
            _create(financial_app, "immutable-request", **changed)
        assert len(_ledger(financial_app)) == 1


def test_cross_tenant_fund_link_cannot_create_financial_record(financial_app):
    app = financial_app
    with app.app_context():
        other = Organization(name="Other tenant", slug="other-tenant")
        db.session.add(other)
        db.session.flush()
        foreign_fund = Fund(name="Other fund", organization_id=other.id)
        db.session.add(foreign_fund)
        db.session.commit()
        with pytest.raises(ValueError, match="Fund"):
            DonationService().create_donation(
                app.config["TEST_ORG_ID"],
                "Verified donor",
                12,
                fund_id=foreign_fund.id,
                reference_number="cross-tenant",
            )
        assert _ledger(app) == []


def test_status_and_audit_roll_back_together(financial_app):
    app = financial_app
    with app.app_context():
        donation = _create(app, "refund-failure")

        def fail_audit(_mapper, _connection, _target):
            raise OperationalError("audit", {}, RuntimeError("injected status failure"))

        event.listen(SecurityAuditEvent, "before_insert", fail_audit)
        try:
            with pytest.raises(OperationalError, match="injected"):
                DonationService().update_status(
                    donation.id, app.config["TEST_ORG_ID"], "refunded"
                )
        finally:
            event.remove(SecurityAuditEvent, "before_insert", fail_audit)
        db.session.refresh(donation)
        assert donation.status == "received"
        assert SecurityAuditEvent.query.count() == 1
        DonationService().update_status(
            donation.id, app.config["TEST_ORG_ID"], "refunded"
        )
        assert SecurityAuditEvent.query.count() == 2
        assert SecurityAuditService.verify_chain_integrity(app.config["TEST_ORG_ID"])


@pytest.mark.parametrize("amount", ["nan", "Infinity", "-Infinity", "1.001"])
def test_reconciliation_rejects_non_money(financial_app, amount):
    with (
        financial_app.app_context(),
        pytest.raises(ValueError, match="Invalid reconciliation amount"),
    ):
        BankReconciliationService().reconcile([{"amount": amount}], [])


def test_reconciliation_conserves_multiplicity_for_all_input_orders(financial_app):
    with financial_app.app_context():
        entries = [
            {"tx_id": "one", "amount": "10"},
            {"tx_id": "two", "amount": "10"},
            {"amount": "20"},
        ]
        svc = BankReconciliationService()
        for bank in permutations(entries):
            for ledger in permutations(entries):
                result = svc.reconcile(list(bank), list(ledger))
                assert result["status"] == "balanced"
                assert result["matched_count"] == 3
                assert result["matched_total"] == 40.0


def test_authenticated_donation_route_replay_and_conflict(financial_app):
    from ngo_homesuite.web.app.routes.donations import donations_bp

    app = financial_app
    app.config["SECRET_KEY"] = "financial-test-only"
    login = LoginManager(app)

    @login.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    app.register_blueprint(donations_bp)
    with app.app_context():
        user = User(
            username="financial-staff",
            email="financial@example.org",
            password_hash="unused-test-hash",
            role="staff",
            organization_id=app.config["TEST_ORG_ID"],
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    request = {
        "donor_name": "Staff entry",
        "amount": 10,
        "reference_number": "api-retry",
    }
    first = client.post("/donations/", json=request)
    retry = client.post("/donations/", json=request)
    assert first.status_code == retry.status_code == 201
    assert first.json["id"] == retry.json["id"]
    conflict = client.post("/donations/", json={**request, "amount": 11})
    assert conflict.status_code == 409
    assert "reference" in conflict.json["error"]
    with app.app_context():
        assert len(_ledger(app)) == 1
        assert SecurityAuditEvent.query.one().actor_user_id == user_id


def test_simultaneous_stripe_webhooks_use_the_same_domain_transaction(financial_app):
    app = financial_app
    gate = Barrier(6)
    payload = {
        "id": "checkout-concurrent",
        "payment_intent": "stripe-same-payment",
        "payment_status": "paid",
        "amount_total": 1234,
        "currency": "usd",
        "metadata": {"org_id": str(app.config["TEST_ORG_ID"])},
        "customer_details": {"name": "Verified donor"},
    }

    def submit(_):
        with app.app_context():
            gate.wait(timeout=10)
            return PaymentService().handle_checkout_completed(payload).id

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(submit, range(6)))
    assert len(set(ids)) == 1
    with app.app_context():
        assert _ledger(app) == [{"tx_id": "stripe-same-payment", "amount": 12.34}]
        with pytest.raises(WebhookProcessingError, match="reference"):
            PaymentService().handle_checkout_completed(
                {**payload, "amount_total": 2000}
            )
        assert Donation.query.one().amount == 12.34


def test_successful_lifecycle_has_one_audit_per_mutation(financial_app):
    app = financial_app
    with app.app_context():
        svc = DonationService()
        donation = _create(app, "audited-lifecycle")
        svc.update_donation(
            donation.id, app.config["TEST_ORG_ID"], notes="Staff reviewed"
        )
        svc.update_status(donation.id, app.config["TEST_ORG_ID"], "processed")
        receipt = svc.generate_receipt(donation.id, app.config["TEST_ORG_ID"])
        assert (
            svc.generate_receipt(donation.id, app.config["TEST_ORG_ID"]).id
            == receipt.id
        )
        svc.update_status(donation.id, app.config["TEST_ORG_ID"], "refunded")
        actions = [
            a.action for a in SecurityAuditEvent.query.order_by(SecurityAuditEvent.id)
        ]
        assert actions == [
            "donation.create",
            "donation.update",
            "donation.status",
            "donation.receipt",
            "donation.status",
        ]
        assert SecurityAuditService.verify_chain_integrity(app.config["TEST_ORG_ID"])
        assert donation.status == "refunded"


def test_reference_cannot_be_edited_to_bypass_retry_identity(financial_app):
    with financial_app.app_context():
        donation = _create(financial_app, "immutable-edit")
        with pytest.raises(ValueError, match="reference"):
            DonationService().update_donation(
                donation.id,
                financial_app.config["TEST_ORG_ID"],
                reference_number="new-reference",
            )
        assert _create(financial_app, "immutable-edit").id == donation.id
        assert len(_ledger(financial_app)) == 1


def _recurring_plan(app):
    donor = Donor(
        name="Recurring donor",
        email="recurring@example.org",
        organization_id=app.config["TEST_ORG_ID"],
    )
    db.session.add(donor)
    db.session.commit()
    return DonationService().create_recurring_plan(
        app.config["TEST_ORG_ID"],
        donor.id,
        amount=10.25,
        currency="USD",
        payment_method="bank_transfer",
        purpose="Recurring proof",
        frequency="monthly",
        next_charge_date=date(2026, 1, 1),
    )


def test_recurring_job_audit_failure_retries_pending_collection_atomically(
    financial_app,
):
    app = financial_app
    with app.app_context():
        plan = _recurring_plan(app)
        plan_id = plan.id

        def fail(_mapper, _connection, _target):
            raise OperationalError(
                "audit", {}, RuntimeError("injected recurring failure")
            )

        event.listen(SecurityAuditEvent, "before_insert", fail)
        try:
            with pytest.raises(OperationalError, match="injected"):
                DonationService().process_due_recurring_plans(
                    app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
                )
        finally:
            event.remove(SecurityAuditEvent, "before_insert", fail)
        assert (
            Donation.query.count()
            == DonationReceipt.query.count()
            == SecurityAuditEvent.query.count()
            == 0
        )
        assert db.session.get(RecurringDonationPlan, plan_id).next_charge_date == date(
            2026, 1, 1
        )
        svc = DonationService()
        assert (
            svc.process_due_recurring_plans(
                app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
            )["processed"]
            == 1
        )
        assert (
            svc.process_due_recurring_plans(
                app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
            )["processed"]
            == 0
        )
        assert len(_ledger(app)) == 1
        assert DonationReceipt.query.count() == 0
        assert Donation.query.one().status == "pending"
        assert db.session.get(RecurringDonationPlan, plan_id).next_charge_date == date(
            2026, 1, 1
        )


def test_simultaneous_recurring_jobs_cannot_duplicate_pending_collection(financial_app):
    app = financial_app
    with app.app_context():
        _recurring_plan(app)
    gate = Barrier(2)

    def run(_):
        with app.app_context():
            gate.wait(timeout=10)
            svc = DonationService()
            try:
                return svc.process_due_recurring_plans(
                    app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
                )["processed"]
            except DonationConcurrencyError:
                return svc.process_due_recurring_plans(
                    app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
                )["processed"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(run, range(2))) == 1
    with app.app_context():
        assert len(_ledger(app)) == 1
        assert DonationReceipt.query.count() == 0
        assert Donation.query.one().status == "pending"


def test_reconciliation_anonymous_entries_cannot_mask_identified_amount_mismatch(
    financial_app,
):
    with financial_app.app_context():
        result = BankReconciliationService().reconcile(
            [{"tx_id": "identified", "amount": 10}, {"amount": 20}],
            [{"tx_id": "identified", "amount": 20}, {"amount": 10}],
        )
        assert result["status"] == "mismatch"
        assert result["matched_count"] == 0


def test_fund_balance_does_not_accumulate_binary_float_rounding_error(financial_app):
    app = financial_app
    with app.app_context():
        amount = Decimal("999999999.99")
        count = 2048
        db.session.add_all(
            [
                Donation(
                    organization_id=app.config["TEST_ORG_ID"],
                    fund_id=app.config["TEST_FUND_ID"],
                    donor_name="Existing ledger entry",
                    amount=float(amount),
                    currency="USD",
                    status="received",
                )
                for _ in range(count)
            ]
        )
        db.session.commit()
        balance = FundService().get_fund_balance(
            app.config["TEST_FUND_ID"], app.config["TEST_ORG_ID"]
        )
        assert Decimal(str(balance["total_in"])) == amount * count
        assert Decimal(str(balance["net_balance"])) == amount * count
