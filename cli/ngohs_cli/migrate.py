# CLI command: homesuite migrate
import os
import sys
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / 'ngo_homesuite' / 'migrations'
DB_PATH = os.getenv('NGO_HOMESUITE_DB_PATH', 'ngo_data.db')


def get_db_connection(db_path):
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def get_applied_versions(conn):
    try:
        cur = conn.execute('SELECT version, hash FROM schema_version ORDER BY version')
        return {row['version']: row['hash'] for row in cur.fetchall()}
    except sqlite3.OperationalError:
        return {}

def hash_sql_file(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

def apply_migration(conn, version, sql_path):
    sql = sql_path.read_text(encoding='utf-8')
    hash_val = hash_sql_file(sql_path)
    conn.executescript(sql)
    conn.execute('INSERT INTO schema_version (version, applied_at_utc, hash) VALUES (?, ?, ?)',
                 (version, datetime.utcnow().isoformat() + 'Z', hash_val))
    conn.commit()
    print(f"Applied migration {version} ({sql_path.name}) with hash {hash_val}")

def migrate():
    conn = get_db_connection(DB_PATH)
    applied = get_applied_versions(conn)
    migration_files = sorted(MIGRATIONS_DIR.glob('*.sql'))
    for mf in migration_files:
        version = int(mf.name.split('_')[0])
        hash_val = hash_sql_file(mf)
        if version in applied:
            if applied[version] != hash_val:
                print(f"ERROR: Migration {mf.name} hash mismatch! DB: {applied[version]} File: {hash_val}")
                sys.exit(1)
            continue
        apply_migration(conn, version, mf)
    print("All migrations applied and verified.")

if __name__ == '__main__':
    migrate()
