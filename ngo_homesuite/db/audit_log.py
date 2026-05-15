# Audit log table and helper for tamper-evident event logging
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_audit_log_table(db_path):
    conn = get_db_connection(db_path)
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                entity TEXT NOT NULL,
                metadata TEXT,
                hash_prev TEXT,
                hash_event TEXT NOT NULL
            )
        ''')
        conn.commit()
    finally:
        conn.close()

def ensure_schema_version_table(db_path):
    conn = get_db_connection(db_path)
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at_utc TEXT NOT NULL,
                hash TEXT NOT NULL
            )
        ''')
        conn.commit()
    finally:
        conn.close()

def log_event(db_path, actor, action, entity, metadata=None):
    ensure_schema_version_table(db_path)
    ensure_audit_log_table(db_path)
    # Hash-verify latest schema version
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute('SELECT version, hash FROM schema_version ORDER BY version DESC LIMIT 1')
        row = cur.fetchone()
        if row:
            version, hash_db = row['version'], row['hash']
            # Find migration file for this version
            from pathlib import Path
            import os
            migrations_dir = Path(__file__).parent.parent / 'migrations'
            migration_file = migrations_dir / f"{str(version).zfill(4)}_initial.sql"
            if migration_file.exists():
                with open(migration_file, 'rb') as f:
                    hash_file = __import__('hashlib').sha256(f.read()).hexdigest()
                if hash_file != hash_db:
                    raise RuntimeError(f"Schema hash mismatch for version {version}! DB: {hash_db} File: {hash_file}")
        cur = conn.execute('SELECT hash_event FROM audit_log ORDER BY id DESC LIMIT 1')
        row = cur.fetchone()
        hash_prev = row['hash_event'] if row else None
        timestamp_utc = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {}, sort_keys=True)
        import hashlib
        event_str = f"{timestamp_utc}|{actor}|{action}|{entity}|{meta_json}|{hash_prev or ''}"
        hash_event = hashlib.sha256(event_str.encode('utf-8')).hexdigest()
        conn.execute(
            'INSERT INTO audit_log (timestamp_utc, actor, action, entity, metadata, hash_prev, hash_event) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (timestamp_utc, actor, action, entity, meta_json, hash_prev, hash_event)
        )
        conn.commit()
    finally:
        conn.close()
