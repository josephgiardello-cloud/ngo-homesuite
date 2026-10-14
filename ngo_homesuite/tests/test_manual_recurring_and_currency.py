import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier, local

import pytest
from flask import Flask
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from ngo_homesuite.audit.security_events import SecurityAuditEvent
from ngo_homesuite.models.core import (
    Donation,
    DonationReceipt,
    Expense,
    Organization,
    RecurringDonationPlan,
    User,
    db,
)
from ngo_homesuite.services.donation_service import (
    DonationConcurrencyError,
    DonationService,
)
from ngo_homesuite.services.fund_service import FundService
from ngo_homesuite.services.reporting_service import ReportingService
from ngo_homesuite.tests.test_financial_failure_correctness import (
    _create,
    _recurring_plan,
    financial_app,
)

__all__ = ["financial_app"]


def _staff(app):
    user = User(
        username="collection-staff",
        email="collection@example.org",
        password_hash="unused",
        role="staff",
        organization_id=app.config["TEST_ORG_ID"],
    )
    db.session.add(user)
    db.session.commit()
    return user.id


def test_due_schedule_does_not_record_received_money_or_issue_receipt(financial_app):
    with financial_app.app_context():
        plan = _recurring_plan(financial_app)
        svc = DonationService()
        svc.process_due_recurring_plans(
            financial_app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        donation = Donation.query.one()
        assert donation.status == "pending"
        assert DonationReceipt.query.count() == 0
        assert plan.next_charge_date == date(2026, 1, 1)
        svc.process_due_recurring_plans(
            financial_app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        assert Donation.query.count() == SecurityAuditEvent.query.count() == 1


def test_mixed_currency_fund_has_no_combined_scalar_total(financial_app):
    app = financial_app
    with app.app_context():
        _create(app, "usd-money", "100", currency="USD")
        _create(app, "eur-money", "50", currency="EUR")
        db.session.add(
            Expense(
                organization_id=app.config["TEST_ORG_ID"],
                fund_id=app.config["TEST_FUND_ID"],
                amount=20,
                currency="USD",
                description="Verified expense",
            )
        )
        db.session.commit()
        result = FundService().get_fund_balance(
            app.config["TEST_FUND_ID"], app.config["TEST_ORG_ID"]
        )
        assert result["total_in"] is None
        assert result["total_out"] is None
        assert result["net_balance"] is None
        assert result["balances_by_currency"] == {
            "EUR": {"total_in": 50.0, "total_out": 0.0, "net_balance": 50.0},
            "USD": {"total_in": 100.0, "total_out": 20.0, "net_balance": 80.0},
        }


def test_manual_confirmation_is_audited_idempotent_and_never_auto_receipted(
    financial_app,
):
    app = financial_app
    with app.app_context():
        plan = _recurring_plan(app)
        actor = _staff(app)
        svc = DonationService()
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        donation = Donation.query.one()
        received = svc.confirm_manual_collection(
            donation.id,
            app.config["TEST_ORG_ID"],
            actor_id=actor,
            collection_reference="bank-statement-row-19",
            amount=10.25,
            currency="USD",
        )
        assert received.status == "received"
        assert plan.next_charge_date == date(2026, 2, 1)
        assert DonationReceipt.query.count() == 0
        assert (
            svc.confirm_manual_collection(
                donation.id,
                app.config["TEST_ORG_ID"],
                actor_id=actor,
                collection_reference="bank-statement-row-19",
                amount=10.25,
                currency="USD",
            ).id
            == donation.id
        )
        assert SecurityAuditEvent.query.count() == 2
        with pytest.raises(ValueError, match="confirmation"):
            svc.confirm_manual_collection(
                donation.id,
                app.config["TEST_ORG_ID"],
                actor_id=actor,
                collection_reference="changed",
                amount=10.25,
                currency="USD",
            )
        assert SecurityAuditEvent.query.count() == 2


def test_confirmation_failure_rolls_back_schedule_money_and_audit(financial_app):
    app = financial_app
    with app.app_context():
        plan = _recurring_plan(app)
        actor = _staff(app)
        svc = DonationService()
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        donation = Donation.query.one()

        def fail(_mapper, _conn, _event):
            raise OperationalError(
                "audit", {}, RuntimeError("injected confirmation failure")
            )

        event.listen(SecurityAuditEvent, "before_insert", fail)
        try:
            with pytest.raises(OperationalError, match="injected"):
                svc.confirm_manual_collection(
                    donation.id,
                    app.config["TEST_ORG_ID"],
                    actor_id=actor,
                    collection_reference="bank-row",
                    amount=10.25,
                    currency="USD",
                )
        finally:
            event.remove(SecurityAuditEvent, "before_insert", fail)
        db.session.refresh(donation)
        db.session.refresh(plan)
        assert donation.status == "pending"
        assert plan.next_charge_date == date(2026, 1, 1)
        assert SecurityAuditEvent.query.count() == 1


@pytest.mark.parametrize("status", ["paused", "cancelled"])
def test_paused_or_cancelled_plan_cannot_be_confirmed(financial_app, status):
    app = financial_app
    with app.app_context():
        plan = _recurring_plan(app)
        actor = _staff(app)
        svc = DonationService()
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        svc.set_manual_collection_plan_status(
            plan.id, app.config["TEST_ORG_ID"], status, actor_id=actor
        )
        with pytest.raises(ValueError, match="active"):
            svc.confirm_manual_collection(
                Donation.query.one().id,
                app.config["TEST_ORG_ID"],
                actor_id=actor,
                collection_reference="bank-row",
                amount=10.25,
                currency="USD",
            )
        assert Donation.query.one().status == "pending"
        assert SecurityAuditEvent.query.count() == 2
        if status == "cancelled":
            with pytest.raises(ValueError, match="cannot be restarted"):
                svc.set_manual_collection_plan_status(
                    plan.id, app.config["TEST_ORG_ID"], "active", actor_id=actor
                )


def test_generic_status_cannot_bypass_manual_confirmation(financial_app):
    app = financial_app
    with app.app_context():
        _recurring_plan(app)
        svc = DonationService()
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        with pytest.raises(ValueError, match="confirmation"):
            svc.update_status(
                Donation.query.one().id, app.config["TEST_ORG_ID"], "received"
            )


def test_simultaneous_confirmations_advance_once(financial_app, monkeypatch):
    app = financial_app
    with app.app_context():
        plan_id = _recurring_plan(app).id
        actor = _staff(app)
        DonationService().schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        donation_id = Donation.query.one().id
    gate = Barrier(2)
    thread = local()
    original = DonationService.get_donation

    def synchronized_load(self, *args, **kwargs):
        donation = original(self, *args, **kwargs)
        if not getattr(thread, "loaded", False):
            thread.loaded = True
            gate.wait(timeout=10)
        return donation

    monkeypatch.setattr(DonationService, "get_donation", synchronized_load)

    def confirm(_):
        with app.app_context():
            svc = DonationService()
            kwargs = {
                "actor_id": actor,
                "collection_reference": "bank-row",
                "amount": 10.25,
                "currency": "USD",
            }
            try:
                return svc.confirm_manual_collection(
                    donation_id, app.config["TEST_ORG_ID"], **kwargs
                ).id
            except DonationConcurrencyError:
                return svc.confirm_manual_collection(
                    donation_id, app.config["TEST_ORG_ID"], **kwargs
                ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(confirm, range(2))) == [donation_id, donation_id]
    with app.app_context():
        assert db.session.get(RecurringDonationPlan, plan_id).next_charge_date == date(
            2026, 2, 1
        )
        assert SecurityAuditEvent.query.count() == 2
        assert DonationReceipt.query.count() == 0


@pytest.mark.parametrize("role,same_tenant", [("viewer", True), ("staff", False)])
def test_confirmation_enforces_service_permission_and_tenant(
    financial_app, role, same_tenant
):
    app = financial_app
    with app.app_context():
        _recurring_plan(app)
        actor_id = _staff(app)
        actor = db.session.get(User, actor_id)
        actor.role = role
        if not same_tenant:
            other = Organization(
                name="Other confirmation tenant", slug="other-confirmation"
            )
            db.session.add(other)
            db.session.flush()
            actor.organization_id = other.id
        db.session.commit()
        svc = DonationService()
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        with pytest.raises(PermissionError):
            svc.confirm_manual_collection(
                Donation.query.one().id,
                app.config["TEST_ORG_ID"],
                actor_id=actor_id,
                collection_reference="bank-row",
                amount=10.25,
                currency="USD",
            )
        assert Donation.query.one().status == "pending"
        assert SecurityAuditEvent.query.count() == 1


def test_pending_and_refunded_money_excluded_from_financial_reports(financial_app):
    app = financial_app
    with app.app_context():
        plan = _recurring_plan(app)
        _create(app, "real-income", 25)
        refunded = _create(app, "refunded-income", 40)
        svc = DonationService()
        svc.update_status(refunded.id, app.config["TEST_ORG_ID"], "refunded")
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        reports = ReportingService()
        assert (
            reports.financial_overview(app.config["TEST_ORG_ID"])["total_donations"]
            == 25
        )
        assert (
            reports.organization_dashboard_summary(app.config["TEST_ORG_ID"])[
                "total_donations"
            ]
            == 25
        )
        assert (
            reports.donor_profile_summary(app.config["TEST_ORG_ID"], plan.donor_id)[
                "donation_total"
            ]
            == 0
        )


def _confirmation_crash_worker(uri, org_id, donation_id, actor_id, stage):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    db.init_app(app)
    with app.app_context():

        def crash(*_):
            os._exit(73)

        if stage == "after_audit_insert":
            event.listen(SecurityAuditEvent, "after_insert", crash)
        else:
            event.listen(db.session(), stage, crash)
        DonationService().confirm_manual_collection(
            int(donation_id),
            int(org_id),
            actor_id=int(actor_id),
            collection_reference="bank-row",
            amount=10.25,
            currency="USD",
        )


@pytest.mark.parametrize(
    "stage", ["before_commit", "after_audit_insert", "after_commit"]
)
def test_confirmation_process_death_retry_preserves_money_schedule_and_audit(
    financial_app, stage
):
    app = financial_app
    with app.app_context():
        plan_id = _recurring_plan(app).id
        actor = _staff(app)
        DonationService().schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        donation_id = Donation.query.one().id
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from ngo_homesuite.tests.test_manual_recurring_and_currency import _confirmation_crash_worker; "
                "import sys; _confirmation_crash_worker(*sys.argv[1:])"
            ),
            app.config["SQLALCHEMY_DATABASE_URI"],
            str(app.config["TEST_ORG_ID"]),
            str(donation_id),
            str(actor),
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
        db.session.remove()
        assert Donation.query.one().status == (
            "received" if stage == "after_commit" else "pending"
        )
        DonationService().confirm_manual_collection(
            donation_id,
            app.config["TEST_ORG_ID"],
            actor_id=actor,
            collection_reference="bank-row",
            amount=10.25,
            currency="USD",
        )
        assert Donation.query.one().status == "received"
        assert db.session.get(RecurringDonationPlan, plan_id).next_charge_date == date(
            2026, 2, 1
        )
        assert SecurityAuditEvent.query.count() == 2
        assert DonationReceipt.query.count() == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"collection_reference": ""},
        {"amount": 10.24},
        {"currency": "EUR"},
    ],
)
def test_confirmation_rejects_missing_evidence_and_mismatched_money(
    financial_app, changes
):
    app = financial_app
    with app.app_context():
        plan = _recurring_plan(app)
        actor = _staff(app)
        svc = DonationService()
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        kwargs = {
            "actor_id": actor,
            "collection_reference": "bank-row",
            "amount": 10.25,
            "currency": "USD",
        }
        with pytest.raises(ValueError):
            svc.confirm_manual_collection(
                Donation.query.one().id, app.config["TEST_ORG_ID"], **(kwargs | changes)
            )
        assert Donation.query.one().status == "pending"
        assert plan.next_charge_date == date(2026, 1, 1)
        assert SecurityAuditEvent.query.count() == 1


def test_failed_collection_retry_and_refund_replay_never_duplicate_income(
    financial_app,
):
    app = financial_app
    with app.app_context():
        _recurring_plan(app)
        actor = _staff(app)
        svc = DonationService()
        svc.schedule_due_manual_collections(
            app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
        )
        donation = Donation.query.one()
        svc.update_status(donation.id, app.config["TEST_ORG_ID"], "failed")
        assert (
            svc.schedule_due_manual_collections(
                app.config["TEST_ORG_ID"], run_date=date(2026, 1, 1)
            )["scheduled"]
            == 0
        )
        kwargs = {
            "actor_id": actor,
            "collection_reference": "cash-book",
            "amount": 10.25,
            "currency": "USD",
        }
        svc.confirm_manual_collection(donation.id, app.config["TEST_ORG_ID"], **kwargs)
        svc.update_status(donation.id, app.config["TEST_ORG_ID"], "refunded")
        assert (
            svc.confirm_manual_collection(
                donation.id, app.config["TEST_ORG_ID"], **kwargs
            ).status
            == "refunded"
        )
        assert Donation.query.count() == 1
        assert DonationReceipt.query.count() == 0
        with pytest.raises(ValueError, match="cannot be deleted"):
            svc.delete_donation(donation.id, app.config["TEST_ORG_ID"])


def test_currency_output_keeps_single_currency_compatibility_and_original_records(
    financial_app,
):
    app = financial_app
    with app.app_context():
        fund = FundService()
        assert (
            fund.get_fund_balance(
                app.config["TEST_FUND_ID"], app.config["TEST_ORG_ID"]
            )["balances_by_currency"]
            == {}
        )
        usd = _create(app, "original-usd", 12.34)
        svc = DonationService()
        pending = _create(app, "pending-eur", 99, currency="EUR", status="pending")
        refunded = _create(app, "refunded-eur", 150, currency="EUR")
        svc.update_status(refunded.id, app.config["TEST_ORG_ID"], "refunded")
        balance = fund.get_fund_balance(
            app.config["TEST_FUND_ID"], app.config["TEST_ORG_ID"]
        )
        assert balance["currency"] == "USD"
        assert balance["total_in"] == balance["net_balance"] == 12.34
        assert balance["balances_by_currency"] == {
            "USD": {"total_in": 12.34, "total_out": 0.0, "net_balance": 12.34}
        }
        assert (usd.amount, usd.currency) == (12.34, "USD")
        assert (pending.amount, pending.currency, pending.status) == (
            99,
            "EUR",
            "pending",
        )
