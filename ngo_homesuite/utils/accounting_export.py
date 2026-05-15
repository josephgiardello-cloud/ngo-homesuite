from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, UTC


def _first_supported_query(cur: sqlite3.Cursor, queries: list[str]) -> list[sqlite3.Row]:
    last_error: sqlite3.Error | None = None
    for sql in queries:
        try:
            cur.execute(sql)
            return cur.fetchall()
        except sqlite3.Error as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return []


def _coerce_amount(raw: float | int | None, *, cents: bool = False) -> float:
    if raw is None:
        return 0.0
    value = float(raw)
    return value / 100.0 if cents else value


def _iso_date(value: str | None) -> str:
    if not value:
        return datetime.now(UTC).strftime("%m/%d/%Y")
    return str(value)[:10]


def export_donations_quickbooks(conn: sqlite3.Connection, csv_path: str) -> int:
    """Export donations to CSV for QuickBooks import workflows."""
    cur = conn.cursor()
    rows = _first_supported_query(
        cur,
        [
            "SELECT id, donor_id, campaign_id, amount, currency, donation_date FROM donations",
            "SELECT id, donor_id, campaign_id, amount_cents, currency, received_at FROM donations",
        ],
    )

    columns = ["id", "donor_id", "campaign_id", "amount", "currency", "date"]
    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(columns)
        for row in rows:
            amount = _coerce_amount(row[3], cents="amount_cents" in cur.description[3][0])
            writer.writerow([row[0], row[1], row[2], amount, row[4], row[5]])
    return len(rows)


def export_expenses_quickbooks(conn: sqlite3.Connection, csv_path: str) -> int:
    """Export expenses to CSV for QuickBooks import workflows."""
    cur = conn.cursor()
    rows = _first_supported_query(
        cur,
        [
            "SELECT id, fund_id, project_id, amount, currency, paid_date FROM expenses",
            "SELECT id, fund_id, project_id, amount_cents, currency, paid_at FROM expenses",
        ],
    )

    columns = ["id", "fund_id", "project_id", "amount", "currency", "date"]
    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(columns)
        for row in rows:
            amount = _coerce_amount(row[3], cents="amount_cents" in cur.description[3][0])
            writer.writerow([row[0], row[1], row[2], amount, row[4], row[5]])
    return len(rows)


def export_donations_quickbooks_iif(
    conn: sqlite3.Connection,
    iif_path: str,
    *,
    bank_account: str = "Undeposited Funds",
    income_account: str = "Donations Income",
) -> int:
    """Export donations in QuickBooks IIF transaction format."""
    cur = conn.cursor()
    rows = _first_supported_query(
        cur,
        [
            "SELECT id, donor_name, amount, donation_date, purpose FROM donations",
            "SELECT id, donor_name, amount_cents, received_at, purpose FROM donations",
        ],
    )

    amount_field_is_cents = "amount_cents" in cur.description[2][0]
    with open(iif_path, "w", encoding="utf-8", newline="") as f:
        f.write("!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO\n")
        f.write("!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO\n")
        f.write("!ENDTRNS\n")

        for row in rows:
            txn_id = row[0]
            donor_name = row[1] or "Unknown Donor"
            amount = _coerce_amount(row[2], cents=amount_field_is_cents)
            txn_date = _iso_date(row[3])
            memo = row[4] or "Donation"
            f.write(f"TRNS\t{txn_id}\tDEPOSIT\t{txn_date}\t{bank_account}\t{donor_name}\t{amount:.2f}\t{memo}\n")
            f.write(f"SPL\t{txn_id}\tDEPOSIT\t{txn_date}\t{income_account}\t{donor_name}\t{-amount:.2f}\t{memo}\n")
            f.write("ENDTRNS\n")
    return len(rows)


def export_expenses_quickbooks_iif(
    conn: sqlite3.Connection,
    iif_path: str,
    *,
    bank_account: str = "Operating Bank",
    expense_account: str = "Program Expense",
) -> int:
    """Export expenses in QuickBooks IIF transaction format."""
    cur = conn.cursor()
    rows = _first_supported_query(
        cur,
        [
            "SELECT id, category, amount, paid_date, notes FROM expenses",
            "SELECT id, category, amount_cents, paid_at, notes FROM expenses",
        ],
    )

    amount_field_is_cents = "amount_cents" in cur.description[2][0]
    with open(iif_path, "w", encoding="utf-8", newline="") as f:
        f.write("!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO\n")
        f.write("!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO\n")
        f.write("!ENDTRNS\n")

        for row in rows:
            txn_id = row[0]
            category = row[1] or "Expense"
            amount = _coerce_amount(row[2], cents=amount_field_is_cents)
            txn_date = _iso_date(row[3])
            memo = row[4] or "Expense"
            f.write(f"TRNS\t{txn_id}\tCHECK\t{txn_date}\t{bank_account}\t{category}\t{-amount:.2f}\t{memo}\n")
            f.write(f"SPL\t{txn_id}\tCHECK\t{txn_date}\t{expense_account}\t{category}\t{amount:.2f}\t{memo}\n")
            f.write("ENDTRNS\n")
    return len(rows)
