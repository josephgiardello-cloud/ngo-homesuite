import csv
import sqlite3
from typing import List, Dict, Any

def import_donors_csv(csv_path: str, conn: sqlite3.Connection, field_map: Dict[str, str]) -> int:
    """
    Import donors from a CSV file. field_map maps CSV columns to DB columns.
    Returns number of imported rows.
    """
    cur = conn.cursor()
    count = 0
    with open(csv_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            db_row = {db_col: row[csv_col] for csv_col, db_col in field_map.items() if csv_col in row}
            columns = ','.join(db_row.keys())
            placeholders = ','.join(['?'] * len(db_row))
            sql = f"INSERT INTO donors ({columns}) VALUES ({placeholders})"
            cur.execute(sql, list(db_row.values()))
            count += 1
    conn.commit()
    return count

def import_donations_csv(csv_path: str, conn: sqlite3.Connection, field_map: Dict[str, str]) -> int:
    """
    Import donations from a CSV file. field_map maps CSV columns to DB columns.
    Returns number of imported rows.
    """
    cur = conn.cursor()
    count = 0
    with open(csv_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            db_row = {db_col: row[csv_col] for csv_col, db_col in field_map.items() if csv_col in row}
            columns = ','.join(db_row.keys())
            placeholders = ','.join(['?'] * len(db_row))
            sql = f"INSERT INTO donations ({columns}) VALUES ({placeholders})"
            cur.execute(sql, list(db_row.values()))
            count += 1
    conn.commit()
    return count

def export_table_csv(table: str, conn: sqlite3.Connection, csv_path: str) -> int:
    """
    Export any table to CSV.
    Returns number of exported rows.
    """
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(columns)
        writer.writerows(rows)
    return len(rows)
