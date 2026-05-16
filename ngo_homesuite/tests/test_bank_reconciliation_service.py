from __future__ import annotations

import pytest

from ngo_homesuite.services.bank_reconciliation_service import BankReconciliationService


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr("ngo_homesuite.services.bank_reconciliation_service.log_event", lambda *args, **kwargs: None)
    return BankReconciliationService()


def test_reconcile_reference_mode_returns_queued_summary(svc):
    result = svc.reconcile(bank_statement="bank_stmt_2026_05", ledger="ledger_2026_05", actor="tester")

    assert result["mode"] == "reference_only"
    assert result["status"] == "queued"
    assert result["bank_statement_ref"] == "bank_stmt_2026_05"
    assert result["ledger_ref"] == "ledger_2026_05"


def test_reconcile_structured_balanced_statement(svc):
    bank_statement = [
        {"tx_id": "TX-1", "amount": "100.00", "source": "bank"},
        {"tx_id": "TX-2", "amount": "50.00", "source": "bank"},
    ]
    ledger = [
        {"tx_id": "TX-1", "amount": 100},
        {"tx_id": "TX-2", "amount": 50.0},
    ]

    result = svc.reconcile(bank_statement=bank_statement, ledger=ledger, actor="tester")

    assert result["mode"] == "structured"
    assert result["status"] == "balanced"
    assert result["matched_count"] == 2
    assert result["matched_total"] == 150.0
    assert result["unmatched_bank_count"] == 0
    assert result["unmatched_ledger_count"] == 0


def test_reconcile_structured_mismatch_reports_unmatched_entries(svc):
    bank_statement = [
        {"tx_id": "TX-1", "amount": "100.00"},
        {"tx_id": "TX-2", "amount": "30.00"},
    ]
    ledger = [
        {"tx_id": "TX-1", "amount": "100.00"},
        {"tx_id": "TX-3", "amount": "40.00"},
    ]

    result = svc.reconcile(bank_statement=bank_statement, ledger=ledger, actor="tester")

    assert result["status"] == "mismatch"
    assert result["matched_count"] == 1
    assert result["unmatched_bank_count"] == 1
    assert result["unmatched_bank_total"] == 30.0
    assert result["unmatched_ledger_count"] == 1
    assert result["unmatched_ledger_total"] == 40.0


def test_reconcile_rejects_invalid_structured_amount(svc):
    with pytest.raises(ValueError, match="Invalid reconciliation amount"):
        svc.reconcile(bank_statement=[{"amount": "not-a-number"}], ledger=[{"amount": 1}], actor="tester")
