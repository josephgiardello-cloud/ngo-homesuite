import csv
import sqlite3
from typing import List

def export_donations_quickbooks(conn: sqlite3.Connection, csv_path: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id, donor_id, campaign_id, amount_cents, currency, received_at FROM donations")
    rows = cur.fetchall()
    columns = ['id', 'donor_id', 'campaign_id', 'amount', 'currency', 'date']
    with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(columns)
        for r in rows:
            writer.writerow([r[0], r[1], r[2], r[3]/100.0, r[4], r[5]])
    return len(rows)

def export_expenses_quickbooks(conn: sqlite3.Connection, csv_path: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id, fund_id, project_id, amount_cents, currency, paid_at FROM expenses")
    rows = cur.fetchall()
    columns = ['id', 'fund_id', 'project_id', 'amount', 'currency', 'date']
    with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(columns)
        for r in rows:
            writer.writerow([r[0], r[1], r[2], r[3]/100.0, r[4], r[5]])
    return len(rows)
