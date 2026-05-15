from __future__ import annotations

import sqlite3

from ngo_homesuite.utils.accounting_export import (
    export_donations_quickbooks,
    export_expenses_quickbooks,
    export_donations_quickbooks_iif,
    export_expenses_quickbooks_iif,
)


def test_export_donations_quickbooks_csv_with_amount_schema(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE donations (
            id INTEGER PRIMARY KEY,
            donor_id INTEGER,
            campaign_id INTEGER,
            amount REAL,
            currency TEXT,
            donation_date TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO donations (id, donor_id, campaign_id, amount, currency, donation_date) VALUES (1, 10, 2, 25.5, 'USD', '2026-05-15')"
    )
    out = tmp_path / "donations.csv"

    count = export_donations_quickbooks(conn, str(out))

    assert count == 1
    text = out.read_text(encoding="utf-8")
    assert "id,donor_id,campaign_id,amount,currency,date" in text
    assert "1,10,2,25.5,USD,2026-05-15" in text


def test_export_expenses_quickbooks_csv_with_cents_schema(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY,
            fund_id INTEGER,
            project_id INTEGER,
            amount_cents INTEGER,
            currency TEXT,
            paid_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO expenses (id, fund_id, project_id, amount_cents, currency, paid_at) VALUES (5, 7, 3, 12345, 'USD', '2026-05-16')"
    )
    out = tmp_path / "expenses.csv"

    count = export_expenses_quickbooks(conn, str(out))

    assert count == 1
    text = out.read_text(encoding="utf-8")
    assert "id,fund_id,project_id,amount,currency,date" in text
    assert "5,7,3,123.45,USD,2026-05-16" in text


def test_export_donations_quickbooks_iif(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE donations (
            id INTEGER PRIMARY KEY,
            donor_name TEXT,
            amount REAL,
            donation_date TEXT,
            purpose TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO donations (id, donor_name, amount, donation_date, purpose) VALUES (1, 'Jane Donor', 50.0, '2026-05-15', 'General Fund')"
    )
    out = tmp_path / "donations.iif"

    count = export_donations_quickbooks_iif(conn, str(out))

    assert count == 1
    text = out.read_text(encoding="utf-8")
    assert "!TRNS" in text
    assert "TRNS\t1\tDEPOSIT" in text
    assert "SPL\t1\tDEPOSIT" in text
    assert "ENDTRNS" in text


def test_export_expenses_quickbooks_iif(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY,
            category TEXT,
            amount REAL,
            paid_date TEXT,
            notes TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO expenses (id, category, amount, paid_date, notes) VALUES (2, 'Supplies', 20.0, '2026-05-14', 'Office supplies')"
    )
    out = tmp_path / "expenses.iif"

    count = export_expenses_quickbooks_iif(conn, str(out))

    assert count == 1
    text = out.read_text(encoding="utf-8")
    assert "!TRNS" in text
    assert "TRNS\t2\tCHECK" in text
    assert "SPL\t2\tCHECK" in text
    assert "ENDTRNS" in text
