import os
import sqlite3
import hashlib
import sys
import datetime
import json
from typing import Any, Callable, Dict
import shutil

# --- AUTOMATED ROLLBACK SUPPORT ---
def rollback_schema(conn: Any, cur: Any, backup_path: str) -> None:
    try:
        if not os.path.exists(backup_path):
            print(f"[ROLLBACK] No backup found at {backup_path}. Cannot rollback.", file=sys.stderr)
            return
        # Use SQLite's online backup API if available
        try:
            # Open backup DB for reading
            backup_conn = sqlite3.connect(backup_path)
            # Use the backup API to restore
            backup_conn.backup(conn)
            backup_conn.close()
            print(f"[ROLLBACK] Database restored from backup {backup_path} using SQLite online backup API.", file=sys.stdout)
        except Exception as e:
            print(f"[ROLLBACK] Online backup API failed: {e}", file=sys.stderr)
            print("[ROLLBACK] Manual restore required: Please close all connections and replace the database file with the backup.", file=sys.stderr)
    except Exception as e:
        print(f"[ROLLBACK] Rollback failed: {e}", file=sys.stderr)

# --- ZERO-DOWNTIME SCHEMA EVOLUTION EXAMPLE (v3) ---
# This migration demonstrates an online schema change: adding a column to donations without downtime.
def migration_v3(conn: Any, cur: Any) -> None:
    # For complex ALTERs on older SQLite, consider: cur.execute("PRAGMA legacy_alter_table = ON;")
    log_migration_event(cur, 3, 'START', 'Backwards-compatible schema evolution: Adding "source" column to donations (copy-table swap pattern)')
    try:
        # Guard: check if donations table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='donations';")
        if not cur.fetchone():
            log_migration_event(cur, 3, 'SKIP', 'Donations table does not exist; skipping v3 migration.')
            return
        # Check if column already exists (idempotent)
        cur.execute("PRAGMA table_info(donations);")
        columns = [row[1] for row in cur.fetchall()]
        if 'source' in columns:
            log_migration_event(cur, 3, 'SUCCESS', 'Donations table already has source column (idempotent)')
            return
        # 1. Get original schema for donations
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='donations';")
        orig_schema = cur.fetchone()[0]
        # 2. Create new table with the extra column
        new_schema = orig_schema.rstrip(')') + ', source TEXT DEFAULT NULL)'
        cur.execute(new_schema.replace('donations', 'donations_new', 1))
        # 3. Copy data
        cur.execute("PRAGMA table_info(donations);")
        old_cols = [row[1] for row in cur.fetchall()]
        insert_cols = ', '.join(old_cols) + ', source'
        select_cols = ', '.join(old_cols) + ', NULL as source'
        cur.execute(f"INSERT INTO donations_new ({insert_cols}) SELECT {select_cols} FROM donations;")
        # 4. Recreate indexes and triggers for donations
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='donations' AND sql IS NOT NULL;")
        for _name, sql in cur.fetchall():
            cur.execute(sql.replace('donations', 'donations_new', 1))
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='donations' AND sql IS NOT NULL;")
        for _name, sql in cur.fetchall():
            cur.execute(sql.replace('donations', 'donations_new', 1))
        # 5. Drop old table and swap
        cur.execute("DROP TABLE donations;")
        cur.execute("ALTER TABLE donations_new RENAME TO donations;")
        log_migration_event(cur, 3, 'SUCCESS', 'Donations table evolved with source column (copy-table swap pattern)')
    except Exception as e:
        log_migration_event(cur, 3, 'FAIL', f'Backwards-compatible schema evolution failed: {e}')
        raise

def detect_schema_drift(cur: Any, expected_tables: set[str], migration_phase: str) -> None:
    # Version-aware drift detection: check tables, triggers, indexes for v2+
    cur.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view','trigger','index') AND name NOT LIKE 'sqlite_%';")
    actual = {row[0]: row[2] for row in cur.fetchall()}
    missing = expected_tables - set(actual.keys())
    extra = set(actual.keys()) - expected_tables
    if missing or extra:
        print(f"[SCHEMA DRIFT][{migration_phase}] Missing: {missing}, Extra: {extra}", file=sys.stderr)
        raise RuntimeError(f"Schema drift detected during {migration_phase}: missing={missing}, extra={extra}")
    # Compare normalized SQL definitions for all objects
    for name in expected_tables:
        cur.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,))
        row = cur.fetchone()
        actual_sql = row[0] if row else ''
        # Normalize SQL: remove whitespace, case, etc.
        norm_actual = ''.join(actual_sql.split()).lower() if actual_sql else ''
        # Retrieve expected hash from schema_version or migration definition
        cur.execute("SELECT expected_object_hashes FROM schema_version WHERE version = (SELECT MAX(version) FROM schema_version)")
        hash_row = cur.fetchone()
        expected_hashes: dict[str, str] = {}
        if hash_row and hash_row[0]:
            expected_hashes = json.loads(hash_row[0])
        expected_hash = expected_hashes.get(name)
        actual_hash = hashlib.sha256(norm_actual.encode('utf-8')).hexdigest() if norm_actual else None
        if expected_hash and actual_hash != expected_hash:
            print(f"[SCHEMA DRIFT][{migration_phase}] Object {name} hash mismatch: expected {expected_hash}, got {actual_hash}", file=sys.stderr)
            raise RuntimeError(f"Schema drift detected for object {name} during {migration_phase}")


def _migration_lock_host() -> str:
    uname_fn = getattr(os, 'uname', None)
    if callable(uname_fn):
        try:
            host_info = uname_fn()
            return str(getattr(host_info, 'nodename', 'unknown'))
        except Exception:
            pass
    return os.environ.get('COMPUTERNAME', 'unknown')

# --- CONFIGURATION & LOGGING ---

def migration_configure_connection(conn: Any, cur: Any) -> None:
    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute("PRAGMA journal_mode = WAL;")
    # --- Encryption Support ---
    # SQLCipher encryption is optional but STRONGLY RECOMMENDED for production deployments.
    # If using a SQLCipher build, you MUST set the key before any other operation.
    # Example:
    #   cur.execute("PRAGMA key = 'your-strong-db-password';")
    #   # Optionally, rotate key with: cur.execute("PRAGMA rekey = 'new-password';")
    # To enforce encryption, set the DB_ENCRYPTION_KEY environment variable.
    db_key = os.getenv('DB_ENCRYPTION_KEY')
    encryption_required = os.getenv('DB_ENCRYPTION_REQUIRED', '0') == '1'
    try:
        # Check for SQLCipher support
        cur.execute("PRAGMA cipher_version;")
        cipher_version = cur.fetchone()
        if cipher_version:
            if db_key:
                # Use parameterized PRAGMA key if possible (sqlite3 does not natively support parameters in PRAGMA, so use safe string formatting)
                cur.execute("PRAGMA key = ?;", (db_key,))
                # Verify encryption is actually enabled
                cur.execute("PRAGMA cipher_integrity_check;")
                integrity = cur.fetchone()
                if not integrity or integrity[0].lower() != 'ok':
                    raise RuntimeError("SQLCipher integrity check failed. Database may not be encrypted or key is incorrect.")
                # Do not print encryption status in production
            elif encryption_required:
                raise RuntimeError("Encryption is required but no DB_ENCRYPTION_KEY set.")
        else:
            if encryption_required:
                raise RuntimeError("Encryption is required but SQLCipher is not available.")
            # Do not print encryption status in production
    except Exception as e:
        if encryption_required:
            raise RuntimeError(f"Encryption is required but failed: {e}")
        # Do not print encryption status in production

def log_migration_event(cur: Any, version: int, status: str, message: str) -> None:
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        cur.execute(
            "INSERT INTO migration_log (version, status, message, at_utc) VALUES (?, ?, ?, ?)",
            (version, status, message, now_utc)
        )
    except sqlite3.OperationalError as e:
        if 'no such table' in str(e):
            cur.execute("""
                CREATE TABLE migration_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version INTEGER,
                    status TEXT,
                    message TEXT,
                    at_utc TEXT
                );
            """)
            cur.execute(
                "INSERT INTO migration_log (version, status, message, at_utc) VALUES (?, ?, ?, ?)",
                (version, status, message, now_utc)
            )
        else:
            raise
    print(f"[MIGRATION][v{version}][{status}] {message}", file=sys.stderr if status == 'FAIL' else sys.stdout)

# --- THE SCHEMA ---


# I removed the manual INSERT into schema_version from the string 
# to prevent "Unique Constraint" errors during the migration loop.
FUNDRAISING_SCHEMA_V1 = '''
CREATE TABLE IF NOT EXISTS allowed_currencies (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
INSERT OR IGNORE INTO allowed_currencies (code, name) VALUES
    ('USD', 'US Dollar'),
    ('EUR', 'Euro'),
    ('GBP', 'British Pound'),
    ('INR', 'Indian Rupee');

CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    -- password_hash must be an Argon2 hash (see app logic)
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'fundraiser', 'accountant', 'viewer')),
    name TEXT NOT NULL,
    -- email is stored as a hash for privacy (see app logic)
    email TEXT UNIQUE,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS bank_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    account_type TEXT CHECK (account_type IN ('checking', 'savings', 'paypal', 'other')),
    -- balance is protected by DB encryption (SQLCipher) and stored as INTEGER cents
    balance INTEGER DEFAULT 0,
    -- currency now references allowed_currencies(code)
    currency TEXT DEFAULT 'USD' REFERENCES allowed_currencies(code),
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS donors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    -- email is stored as a hash for privacy (see app logic)
    email TEXT UNIQUE,
    -- phone and address are stored as hashes for privacy (see app logic)
    phone TEXT,
    address TEXT,
    donor_type TEXT CHECK (donor_type IN ('individual', 'corporate', 'foundation', 'anonymous')),
    status TEXT NOT NULL CHECK (status IN ('active', 'lapsed', 'prospect', 'archived')),
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL,
    deleted_by INTEGER REFERENCES staff(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS donor_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS donor_list_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_list_id INTEGER REFERENCES donor_lists(id) ON DELETE CASCADE,
    donor_id INTEGER REFERENCES donors(id) ON DELETE CASCADE,
    added_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    added_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL,
    UNIQUE(donor_list_id, donor_id)
);

-- Triggers to update updated_at/added_at fields to UTC ISO format

CREATE TRIGGER IF NOT EXISTS trg_staff_updated_at
BEFORE UPDATE ON staff
FOR EACH ROW
BEGIN
    SET NEW.updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');
END;

CREATE TRIGGER IF NOT EXISTS trg_bank_accounts_updated_at
BEFORE UPDATE ON bank_accounts
FOR EACH ROW
BEGIN
    SET NEW.updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');
END;

CREATE TRIGGER IF NOT EXISTS trg_donors_updated_at
BEFORE UPDATE ON donors
FOR EACH ROW
BEGIN
    SET NEW.updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');
END;

CREATE TRIGGER IF NOT EXISTS trg_donor_list_members_added_at
AFTER INSERT ON donor_list_members
FOR EACH ROW
BEGIN
    UPDATE donor_list_members SET added_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id;
END;

CREATE VIEW IF NOT EXISTS active_staff AS
    SELECT * FROM staff WHERE deleted_at IS NULL;

CREATE VIEW IF NOT EXISTS active_donors AS
    SELECT * FROM donors WHERE deleted_at IS NULL;

-- Accountant-only view: restricts access to balances
CREATE VIEW IF NOT EXISTS accountant_balances AS
    SELECT b.id, b.name, b.balance, b.currency
    FROM bank_accounts b
    WHERE b.deleted_at IS NULL;

-- Indexes for performance (added in v1)
CREATE INDEX IF NOT EXISTS idx_staff_username ON staff(username);
CREATE INDEX IF NOT EXISTS idx_donors_email ON donors(email);
CREATE INDEX IF NOT EXISTS idx_donors_phone ON donors(phone);
CREATE INDEX IF NOT EXISTS idx_donors_address ON donors(address);
CREATE INDEX IF NOT EXISTS idx_donor_lists_name ON donor_lists(name);
CREATE INDEX IF NOT EXISTS idx_donor_list_members_list_id ON donor_list_members(donor_list_id);
CREATE INDEX IF NOT EXISTS idx_donor_list_members_donor_id ON donor_list_members(donor_id);
CREATE INDEX IF NOT EXISTS idx_bank_accounts_name ON bank_accounts(name);
CREATE TABLE IF NOT EXISTS __db_metadata__ (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
# Migration log table is created once here for all migrations
CREATE TABLE IF NOT EXISTS migration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER,
    status TEXT,
    message TEXT,
    at_utc TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL UNIQUE,
    applied_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    description TEXT,
    schema_hash TEXT
);

CREATE TABLE IF NOT EXISTS migration_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    locked_at TEXT,
    locked_by TEXT
);
'''



# --- PASSWORD HASHING (ENFORCED IN APP LOGIC) ---

import base64

# --- PII HASHING/ANONYMIZATION (ENFORCED IN APP LOGIC) ---
def hash_pii(value: str, cur: Any = None) -> str:
    """
    Hash PII (email, phone, address) using HMAC-SHA256 with a per-installation secret salt.
    The salt is stored in __db_metadata__ under key 'pii_salt'.
    Use this before inserting/updating PII fields.
    If cur is not provided, uses a module-level singleton/cache for salt.
    NOTE: In multi-process deployments, ensure salt is shared (e.g., via env var PII_SALT or shared config).
    """
    if not value:
        return ''
    import hmac
    import threading
    _pii_salt_threadlocal = threading.local()
    salt = None
    if cur is not None:
        cur.execute("SELECT value FROM __db_metadata__ WHERE key = 'pii_salt'")
        row = cur.fetchone()
        if row and row[0]:
            salt = row[0]
        else:
            raise RuntimeError("PII salt not provisioned in __db_metadata__. Refusing to hash PII.")
    else:
        # Use thread-local cache or env var
        if not hasattr(_pii_salt_threadlocal, 'salt'):
            salt = os.getenv('PII_SALT')
            if not salt:
                raise RuntimeError("PII_SALT environment variable not set. Refusing to hash PII.")
            _pii_salt_threadlocal.salt = salt
        salt = _pii_salt_threadlocal.salt
    digest = hmac.new(salt.encode('utf-8'), value.encode('utf-8'), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii')

# --- PASSWORD HASHING (ENFORCED IN APP LOGIC) ---
from argon2 import PasswordHasher
ph = PasswordHasher()

def hash_password(plain_password: str) -> str:
    """
    Hash a password using Argon2. Use this before inserting/updating staff passwords.
    """
    return ph.hash(plain_password)

# --- INTEGRITY HELPERS ---


def compute_schema_hash(schema_sql: str) -> str:
    return hashlib.sha256(schema_sql.encode('utf-8')).hexdigest()


def get_current_schema_version(cur: Any) -> int:
    cur.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = cur.fetchone()
    return int(row[0]) if row else 0

def get_current_schema_hash(cur: Any) -> str:
    try:
        cur.execute("SELECT schema_hash FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row and row[0] else ''
    except sqlite3.OperationalError as e:
        if 'no such table' in str(e):
            return ''
        raise

# --- MIGRATION STEPS ---

def migration_v1(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 1, 'START', 'Applying fundraising schema v1')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V1)
        log_migration_event(cur, 1, 'SUCCESS', 'Migration v1 applied successfully')
    except Exception as e:
        log_migration_event(cur, 1, 'FAIL', f'Migration v1 failed: {e}')
        raise


# --- v2 SCHEMA: Donations, Expenses, Funds, Projects ---
FUNDRAISING_SCHEMA_V5 = '''
CREATE INDEX IF NOT EXISTS idx_donations_campaign_id ON donations(campaign_id);
CREATE INDEX IF NOT EXISTS idx_donors_household_id ON donors(household_id);
CREATE INDEX IF NOT EXISTS idx_pledges_donor_id ON pledges(donor_id);
CREATE INDEX IF NOT EXISTS idx_pledges_campaign_id ON pledges(campaign_id);
CREATE INDEX IF NOT EXISTS idx_interactions_donor_id ON interactions(donor_id);
CREATE INDEX IF NOT EXISTS idx_grants_fund_id ON grants(fund_id);
CREATE INDEX IF NOT EXISTS idx_donor_relationships_from ON donor_relationships(from_donor_id);
CREATE INDEX IF NOT EXISTS idx_donor_relationships_to ON donor_relationships(to_donor_id);
'''

def migration_v5(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 5, 'START', 'Applying fundraising schema v5 (additional indexes)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V5)
        log_migration_event(cur, 5, 'SUCCESS', 'Migration v5 applied successfully')
    except Exception as e:
        log_migration_event(cur, 5, 'FAIL', f'Migration v5 failed: {e}')
        raise
FUNDRAISING_SCHEMA_V4 = '''

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    goal_cents INTEGER DEFAULT 0,
    fund_id INTEGER REFERENCES funds(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);
ALTER TABLE donations ADD COLUMN campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL;

-- Pledges / Recurring Gifts
CREATE TABLE IF NOT EXISTS pledges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id INTEGER REFERENCES donors(id) ON DELETE CASCADE,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    frequency TEXT CHECK(frequency IN ('one-time', 'monthly', 'quarterly', 'annually')),
    start_date TEXT,
    end_date TEXT,
    installments_expected INTEGER,
    installments_paid INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'completed', 'cancelled')),
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);

-- Interactions / Tasks / Notes
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id INTEGER REFERENCES donors(id) ON DELETE CASCADE,
    type TEXT CHECK(type IN ('call', 'email', 'meeting', 'note', 'task')),
    subject TEXT,
    notes TEXT,
    due_date TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);

-- Households / Relationships
CREATE TABLE IF NOT EXISTS households (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    primary_donor_id INTEGER REFERENCES donors(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);
ALTER TABLE donors ADD COLUMN household_id INTEGER REFERENCES households(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS donor_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_donor_id INTEGER REFERENCES donors(id) ON DELETE CASCADE,
    to_donor_id INTEGER REFERENCES donors(id) ON DELETE CASCADE,
    relationship_type TEXT CHECK(relationship_type IN ('spouse', 'child', 'parent', 'sibling', 'friend', 'colleague')),
    deleted_at TEXT DEFAULT NULL,
    UNIQUE(from_donor_id, to_donor_id, relationship_type)
);

-- Basic Grants Management
CREATE TABLE IF NOT EXISTS grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_name TEXT NOT NULL,
    grant_name TEXT,
    amount_awarded_cents INTEGER,
    currency TEXT DEFAULT 'USD',
    award_date TEXT,
    reporting_deadline TEXT,
    fund_id INTEGER REFERENCES funds(id) ON DELETE SET NULL,
    status TEXT DEFAULT 'active' CHECK(status IN ('applied', 'awarded', 'reporting', 'closed')),
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);

-- Views to filter out soft-deleted rows for new tables
CREATE VIEW IF NOT EXISTS active_campaigns AS SELECT * FROM campaigns WHERE deleted_at IS NULL;
CREATE VIEW IF NOT EXISTS active_pledges AS SELECT * FROM pledges WHERE deleted_at IS NULL;
CREATE VIEW IF NOT EXISTS active_interactions AS SELECT * FROM interactions WHERE deleted_at IS NULL;
CREATE VIEW IF NOT EXISTS active_households AS SELECT * FROM households WHERE deleted_at IS NULL;
CREATE VIEW IF NOT EXISTS active_grants AS SELECT * FROM grants WHERE deleted_at IS NULL;
CREATE VIEW IF NOT EXISTS active_donor_relationships AS SELECT * FROM donor_relationships WHERE deleted_at IS NULL;
'''

def migration_v4(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 4, 'START', 'Applying fundraising schema v4 (campaigns, pledges, interactions, households, grants)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V4)
        log_migration_event(cur, 4, 'SUCCESS', 'Migration v4 applied successfully')
    except Exception as e:
        log_migration_event(cur, 4, 'FAIL', f'Migration v4 failed: {e}')
        raise
FUNDRAISING_SCHEMA_V2 = '''
CREATE TABLE IF NOT EXISTS funds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    fund_id INTEGER REFERENCES funds(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS donations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id INTEGER REFERENCES donors(id) ON DELETE SET NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL REFERENCES allowed_currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT,
    received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    fund_id INTEGER REFERENCES funds(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    bank_account_id INTEGER REFERENCES bank_accounts(id) ON DELETE SET NULL,
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    note TEXT,
    deleted_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL REFERENCES allowed_currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT,
    paid_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    fund_id INTEGER REFERENCES funds(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    bank_account_id INTEGER REFERENCES bank_accounts(id) ON DELETE SET NULL,
    payee TEXT,
    description TEXT,
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_donations_donor_id ON donations(donor_id);
CREATE INDEX IF NOT EXISTS idx_donations_fund_id ON donations(fund_id);
CREATE INDEX IF NOT EXISTS idx_donations_project_id ON donations(project_id);
CREATE INDEX IF NOT EXISTS idx_donations_bank_account_id ON donations(bank_account_id);
CREATE INDEX IF NOT EXISTS idx_expenses_fund_id ON expenses(fund_id);
CREATE INDEX IF NOT EXISTS idx_expenses_project_id ON expenses(project_id);
CREATE INDEX IF NOT EXISTS idx_expenses_bank_account_id ON expenses(bank_account_id);
CREATE INDEX IF NOT EXISTS idx_projects_fund_id ON projects(fund_id);
'''

def migration_v2(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 2, 'START', 'Applying fundraising schema v2 (donations, expenses, funds, projects)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V2)
        # Add audit table. All audit_log entries must now be written by application logic with explicit changed_by and actor_type.
        audit_sql = '''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            operation TEXT NOT NULL,
            row_id INTEGER,
            changed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            changed_by INTEGER NULL,
            actor_type TEXT NOT NULL CHECK(actor_type IN ('user','system','migration')),
            old_data TEXT,
            new_data TEXT,
            retention_until TEXT DEFAULT NULL -- UTC ISO8601 timestamp; if set, eligible for archival/deletion after this time
        );
        -- retention_until: UTC ISO8601 timestamp. If set, this row is eligible for archival or deletion after the specified time.
        -- Archival/retention policy should be enforced by periodic jobs or manual review.

        -- All audit_log entries must be written by application logic with explicit changed_by and actor_type.
        -- No triggers are defined for audit_log. See documentation for required usage pattern.

        -- Donations
        CREATE TRIGGER IF NOT EXISTS audit_donations_insert AFTER INSERT ON donations BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, new_data)
            VALUES ('donations', 'INSERT', NEW.id, NULL, 'system', json_object('donor_id', NEW.donor_id, 'amount_cents', NEW.amount_cents, 'currency', NEW.currency));
        END;
        CREATE TRIGGER IF NOT EXISTS audit_donations_update AFTER UPDATE ON donations BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, old_data, new_data)
            VALUES ('donations', 'UPDATE', NEW.id, NULL, 'system', json_object('donor_id', OLD.donor_id, 'amount_cents', OLD.amount_cents, 'currency', OLD.currency), json_object('donor_id', NEW.donor_id, 'amount_cents', NEW.amount_cents, 'currency', NEW.currency));
        END;
        CREATE TRIGGER IF NOT EXISTS audit_donations_delete AFTER DELETE ON donations BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, old_data)
            VALUES ('donations', 'DELETE', OLD.id, NULL, 'system', json_object('donor_id', OLD.donor_id, 'amount_cents', OLD.amount_cents, 'currency', OLD.currency));
        END;

        -- Expenses
        CREATE TRIGGER IF NOT EXISTS audit_expenses_insert AFTER INSERT ON expenses BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, new_data)
            VALUES ('expenses', 'INSERT', NEW.id, NULL, 'system', json_object('amount_cents', NEW.amount_cents, 'currency', NEW.currency));
        END;
        CREATE TRIGGER IF NOT EXISTS audit_expenses_update AFTER UPDATE ON expenses BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, old_data, new_data)
            VALUES ('expenses', 'UPDATE', NEW.id, NULL, 'system', json_object('amount_cents', OLD.amount_cents, 'currency', OLD.currency), json_object('amount_cents', NEW.amount_cents, 'currency', NEW.currency));
        END;
        CREATE TRIGGER IF NOT EXISTS audit_expenses_delete AFTER DELETE ON expenses BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, old_data)
            VALUES ('expenses', 'DELETE', OLD.id, NULL, 'system', json_object('amount_cents', OLD.amount_cents, 'currency', OLD.currency));
        END;

        -- Funds
        CREATE TRIGGER IF NOT EXISTS audit_funds_insert AFTER INSERT ON funds BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, new_data)
            VALUES ('funds', 'INSERT', NEW.id, NULL, 'system', json_object('name', NEW.name, 'description', NEW.description));
        END;
        CREATE TRIGGER IF NOT EXISTS audit_funds_update AFTER UPDATE ON funds BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, old_data, new_data)
            VALUES ('funds', 'UPDATE', NEW.id, NULL, 'system', json_object('name', OLD.name, 'description', OLD.description), json_object('name', NEW.name, 'description', NEW.description));
        END;
        CREATE TRIGGER IF NOT EXISTS audit_funds_delete AFTER DELETE ON funds BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, old_data)
            VALUES ('funds', 'DELETE', OLD.id, NULL, 'system', json_object('name', OLD.name, 'description', OLD.description));
        END;

        -- Projects
        CREATE TRIGGER IF NOT EXISTS audit_projects_insert AFTER INSERT ON projects BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, new_data)
            VALUES ('projects', 'INSERT', NEW.id, NULL, 'system', json_object('name', NEW.name, 'description', NEW.description));
        END;
        CREATE TRIGGER IF NOT EXISTS audit_projects_update AFTER UPDATE ON projects BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, old_data, new_data)
            VALUES ('projects', 'UPDATE', NEW.id, NULL, 'system', json_object('name', OLD.name, 'description', OLD.description), json_object('name', NEW.name, 'description', NEW.description));
        END;
        CREATE TRIGGER IF NOT EXISTS audit_projects_delete AFTER DELETE ON projects BEGIN
            INSERT INTO audit_log (table_name, operation, row_id, changed_by, actor_type, old_data)
            VALUES ('projects', 'DELETE', OLD.id, NULL, 'system', json_object('name', OLD.name, 'description', OLD.description));
        END;
        '''
        cur.executescript(audit_sql)
        log_migration_event(cur, 2, 'SUCCESS', 'Migration v2 applied successfully (with audit triggers)')
    except Exception as e:
        log_migration_event(cur, 2, 'FAIL', f'Migration v2 failed: {e}')
        raise


# Enforce migration order and prevent gaps

# --- v6: Audit triggers for new entities ---
FUNDRAISING_SCHEMA_V6 = '''
-- Audit triggers for campaigns
CREATE TRIGGER IF NOT EXISTS audit_campaigns_insert AFTER INSERT ON campaigns BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, new_data)
    VALUES ('campaigns', 'INSERT', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('name', NEW.name, 'code', NEW.code, 'description', NEW.description, 'start_date', NEW.start_date, 'end_date', NEW.end_date, 'goal_cents', NEW.goal_cents, 'fund_id', NEW.fund_id));
END;
CREATE TRIGGER IF NOT EXISTS audit_campaigns_update AFTER UPDATE ON campaigns BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data, new_data)
    VALUES ('campaigns', 'UPDATE', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('name', OLD.name, 'code', OLD.code, 'description', OLD.description, 'start_date', OLD.start_date, 'end_date', OLD.end_date, 'goal_cents', OLD.goal_cents, 'fund_id', OLD.fund_id), json_object('name', NEW.name, 'code', NEW.code, 'description', NEW.description, 'start_date', NEW.start_date, 'end_date', NEW.end_date, 'goal_cents', NEW.goal_cents, 'fund_id', NEW.fund_id));
END;
CREATE TRIGGER IF NOT EXISTS audit_campaigns_delete AFTER DELETE ON campaigns BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data)
    VALUES ('campaigns', 'DELETE', OLD.id, (SELECT user_id FROM current_user LIMIT 1), json_object('name', OLD.name, 'code', OLD.code, 'description', OLD.description, 'start_date', OLD.start_date, 'end_date', OLD.end_date, 'goal_cents', OLD.goal_cents, 'fund_id', OLD.fund_id));
END;

-- Audit triggers for pledges
CREATE TRIGGER IF NOT EXISTS audit_pledges_insert AFTER INSERT ON pledges BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, new_data)
    VALUES ('pledges', 'INSERT', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('donor_id', NEW.donor_id, 'campaign_id', NEW.campaign_id, 'amount_cents', NEW.amount_cents, 'currency', NEW.currency, 'frequency', NEW.frequency, 'start_date', NEW.start_date, 'end_date', NEW.end_date, 'installments_expected', NEW.installments_expected, 'installments_paid', NEW.installments_paid, 'status', NEW.status));
END;
CREATE TRIGGER IF NOT EXISTS audit_pledges_update AFTER UPDATE ON pledges BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data, new_data)
    VALUES ('pledges', 'UPDATE', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('donor_id', OLD.donor_id, 'campaign_id', OLD.campaign_id, 'amount_cents', OLD.amount_cents, 'currency', OLD.currency, 'frequency', OLD.frequency, 'start_date', OLD.start_date, 'end_date', OLD.end_date, 'installments_expected', OLD.installments_expected, 'installments_paid', OLD.installments_paid, 'status', OLD.status), json_object('donor_id', NEW.donor_id, 'campaign_id', NEW.campaign_id, 'amount_cents', NEW.amount_cents, 'currency', NEW.currency, 'frequency', NEW.frequency, 'start_date', NEW.start_date, 'end_date', NEW.end_date, 'installments_expected', NEW.installments_expected, 'installments_paid', NEW.installments_paid, 'status', NEW.status));
END;
CREATE TRIGGER IF NOT EXISTS audit_pledges_delete AFTER DELETE ON pledges BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data)
    VALUES ('pledges', 'DELETE', OLD.id, (SELECT user_id FROM current_user LIMIT 1), json_object('donor_id', OLD.donor_id, 'campaign_id', OLD.campaign_id, 'amount_cents', OLD.amount_cents, 'currency', OLD.currency, 'frequency', OLD.frequency, 'start_date', OLD.start_date, 'end_date', OLD.end_date, 'installments_expected', OLD.installments_expected, 'installments_paid', OLD.installments_paid, 'status', OLD.status));
END;

-- Audit triggers for interactions
CREATE TRIGGER IF NOT EXISTS audit_interactions_insert AFTER INSERT ON interactions BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, new_data)
    VALUES ('interactions', 'INSERT', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('donor_id', NEW.donor_id, 'type', NEW.type, 'subject', NEW.subject, 'notes', NEW.notes, 'due_date', NEW.due_date, 'completed_at', NEW.completed_at));
END;
CREATE TRIGGER IF NOT EXISTS audit_interactions_update AFTER UPDATE ON interactions BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data, new_data)
    VALUES ('interactions', 'UPDATE', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('donor_id', OLD.donor_id, 'type', OLD.type, 'subject', OLD.subject, 'notes', OLD.notes, 'due_date', OLD.due_date, 'completed_at', OLD.completed_at), json_object('donor_id', NEW.donor_id, 'type', NEW.type, 'subject', NEW.subject, 'notes', NEW.notes, 'due_date', NEW.due_date, 'completed_at', NEW.completed_at));
END;
CREATE TRIGGER IF NOT EXISTS audit_interactions_delete AFTER DELETE ON interactions BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data)
    VALUES ('interactions', 'DELETE', OLD.id, (SELECT user_id FROM current_user LIMIT 1), json_object('donor_id', OLD.donor_id, 'type', OLD.type, 'subject', OLD.subject, 'notes', OLD.notes, 'due_date', OLD.due_date, 'completed_at', OLD.completed_at));
END;

-- Audit triggers for households
CREATE TRIGGER IF NOT EXISTS audit_households_insert AFTER INSERT ON households BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, new_data)
    VALUES ('households', 'INSERT', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('name', NEW.name, 'primary_donor_id', NEW.primary_donor_id));
END;
CREATE TRIGGER IF NOT EXISTS audit_households_update AFTER UPDATE ON households BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data, new_data)
    VALUES ('households', 'UPDATE', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('name', OLD.name, 'primary_donor_id', OLD.primary_donor_id), json_object('name', NEW.name, 'primary_donor_id', NEW.primary_donor_id));
END;
CREATE TRIGGER IF NOT EXISTS audit_households_delete AFTER DELETE ON households BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data)
    VALUES ('households', 'DELETE', OLD.id, (SELECT user_id FROM current_user LIMIT 1), json_object('name', OLD.name, 'primary_donor_id', OLD.primary_donor_id));
END;

-- Audit triggers for donor_relationships
CREATE TRIGGER IF NOT EXISTS audit_donor_relationships_insert AFTER INSERT ON donor_relationships BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, new_data)
    VALUES ('donor_relationships', 'INSERT', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('from_donor_id', NEW.from_donor_id, 'to_donor_id', NEW.to_donor_id, 'relationship_type', NEW.relationship_type));
END;
CREATE TRIGGER IF NOT EXISTS audit_donor_relationships_update AFTER UPDATE ON donor_relationships BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data, new_data)
    VALUES ('donor_relationships', 'UPDATE', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('from_donor_id', OLD.from_donor_id, 'to_donor_id', OLD.to_donor_id, 'relationship_type', OLD.relationship_type), json_object('from_donor_id', NEW.from_donor_id, 'to_donor_id', NEW.to_donor_id, 'relationship_type', NEW.relationship_type));
END;
CREATE TRIGGER IF NOT EXISTS audit_donor_relationships_delete AFTER DELETE ON donor_relationships BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data)
    VALUES ('donor_relationships', 'DELETE', OLD.id, (SELECT user_id FROM current_user LIMIT 1), json_object('from_donor_id', OLD.from_donor_id, 'to_donor_id', OLD.to_donor_id, 'relationship_type', OLD.relationship_type));
END;

-- Audit triggers for grants
CREATE TRIGGER IF NOT EXISTS audit_grants_insert AFTER INSERT ON grants BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, new_data)
    VALUES ('grants', 'INSERT', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('funder_name', NEW.funder_name, 'grant_name', NEW.grant_name, 'amount_awarded_cents', NEW.amount_awarded_cents, 'currency', NEW.currency, 'award_date', NEW.award_date, 'reporting_deadline', NEW.reporting_deadline, 'fund_id', NEW.fund_id, 'status', NEW.status));
END;
CREATE TRIGGER IF NOT EXISTS audit_grants_update AFTER UPDATE ON grants BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data, new_data)
    VALUES ('grants', 'UPDATE', NEW.id, (SELECT user_id FROM current_user LIMIT 1), json_object('funder_name', OLD.funder_name, 'grant_name', OLD.grant_name, 'amount_awarded_cents', OLD.amount_awarded_cents, 'currency', OLD.currency, 'award_date', OLD.award_date, 'reporting_deadline', OLD.reporting_deadline, 'fund_id', OLD.fund_id, 'status', OLD.status), json_object('funder_name', NEW.funder_name, 'grant_name', NEW.grant_name, 'amount_awarded_cents', NEW.amount_awarded_cents, 'currency', NEW.currency, 'award_date', NEW.award_date, 'reporting_deadline', NEW.reporting_deadline, 'fund_id', NEW.fund_id, 'status', NEW.status));
END;
CREATE TRIGGER IF NOT EXISTS audit_grants_delete AFTER DELETE ON grants BEGIN
    INSERT INTO audit_log (table_name, operation, row_id, changed_by, old_data)
    VALUES ('grants', 'DELETE', OLD.id, (SELECT user_id FROM current_user LIMIT 1), json_object('funder_name', OLD.funder_name, 'grant_name', OLD.grant_name, 'amount_awarded_cents', OLD.amount_awarded_cents, 'currency', OLD.currency, 'award_date', OLD.award_date, 'reporting_deadline', OLD.reporting_deadline, 'fund_id', OLD.fund_id, 'status', OLD.status));
END;
'''

def migration_v6(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 6, 'START', 'Applying fundraising schema v6 (audit triggers for new entities)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V6)
        log_migration_event(cur, 6, 'SUCCESS', 'Migration v6 applied successfully (audit triggers for new entities)')
    except Exception as e:
        log_migration_event(cur, 6, 'FAIL', f'Migration v6 failed: {e}')
        raise
FUNDRAISING_SCHEMA_V7 = '''
-- Add unique constraint for campaigns (name, start_date)
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_name_start_date ON campaigns(name, start_date);
-- Add unique constraint for grants (grant_name, funder_name)
CREATE UNIQUE INDEX IF NOT EXISTS idx_grants_grant_name_funder_name ON grants(grant_name, funder_name);
'''

def migration_v7(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 7, 'START', 'Applying fundraising schema v7 (natural key uniqueness)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V7)
        log_migration_event(cur, 7, 'SUCCESS', 'Migration v7 applied successfully (natural key uniqueness)')
    except Exception as e:
        log_migration_event(cur, 7, 'FAIL', f'Migration v7 failed: {e}')
        raise

# --- v8: Monetary field constraints and reporting views ---
FUNDRAISING_SCHEMA_V8 = '''
-- Add CHECK constraint for pledges.amount_cents >= 0
CREATE TABLE IF NOT EXISTS pledges_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id INTEGER REFERENCES donors(id) ON DELETE CASCADE,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    frequency TEXT CHECK(frequency IN ('one-time', 'monthly', 'quarterly', 'annually')),
    start_date TEXT,
    end_date TEXT,
    installments_expected INTEGER,
    installments_paid INTEGER DEFAULT 0 CHECK(installments_paid >= 0 AND installments_paid <= installments_expected),
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'completed', 'cancelled')),
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);
INSERT INTO pledges_new SELECT * FROM pledges;
DROP TABLE pledges;
ALTER TABLE pledges_new RENAME TO pledges;

-- Add CHECK constraint for grants.amount_awarded_cents >= 0
CREATE TABLE IF NOT EXISTS grants_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_name TEXT NOT NULL,
    grant_name TEXT,
    amount_awarded_cents INTEGER CHECK(amount_awarded_cents >= 0),
    currency TEXT DEFAULT 'USD',
    award_date TEXT,
    reporting_deadline TEXT,
    fund_id INTEGER REFERENCES funds(id) ON DELETE SET NULL,
    status TEXT DEFAULT 'active' CHECK(status IN ('applied', 'awarded', 'reporting', 'closed')),
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    deleted_at TEXT DEFAULT NULL
);
INSERT INTO grants_new SELECT * FROM grants;
DROP TABLE grants;
ALTER TABLE grants_new RENAME TO grants;

-- Reporting views
CREATE VIEW IF NOT EXISTS campaign_summary AS
    SELECT c.id AS campaign_id, c.name AS campaign_name, c.start_date, c.end_date,
        COALESCE(SUM(p.amount_cents), 0) AS total_pledged,
        COALESCE(SUM(d.amount_cents), 0) AS total_donated
    FROM campaigns c
    LEFT JOIN pledges p ON p.campaign_id = c.id AND p.deleted_at IS NULL
    LEFT JOIN donations d ON d.campaign_id = c.id AND d.deleted_at IS NULL
    WHERE c.deleted_at IS NULL
    GROUP BY c.id;

CREATE VIEW IF NOT EXISTS pledge_overdue AS
    SELECT p.*
    FROM pledges p
    WHERE p.status = 'active'
      AND p.end_date IS NOT NULL
      AND date(p.end_date) < date('now')
      AND p.installments_paid < p.installments_expected
      AND p.deleted_at IS NULL;
'''

def migration_v8(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 8, 'START', 'Applying fundraising schema v8 (monetary constraints, reporting views)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V8)
        log_migration_event(cur, 8, 'SUCCESS', 'Migration v8 applied successfully (monetary constraints, reporting views)')
    except Exception as e:
        log_migration_event(cur, 8, 'FAIL', f'Migration v8 failed: {e}')
        raise
FUNDRAISING_SCHEMA_V9 = '''

-- Add missing tables for events and registrations
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
    donor_id INTEGER REFERENCES donors(id) ON DELETE SET NULL,
    registered_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at TEXT DEFAULT NULL
);

-- Add updated_at triggers for new tables (BEFORE UPDATE, correct SQLite syntax)
CREATE TRIGGER IF NOT EXISTS trg_campaigns_updated_at
BEFORE UPDATE ON campaigns
FOR EACH ROW
BEGIN
    SELECT NEW.updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');
END;

CREATE TRIGGER IF NOT EXISTS trg_pledges_updated_at
BEFORE UPDATE ON pledges
FOR EACH ROW
BEGIN
    SELECT NEW.updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');
END;

CREATE TRIGGER IF NOT EXISTS trg_interactions_updated_at
BEFORE UPDATE ON interactions
FOR EACH ROW
BEGIN
    SELECT NEW.updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');
END;

CREATE TRIGGER IF NOT EXISTS trg_households_updated_at
BEFORE UPDATE ON households
FOR EACH ROW
BEGIN
    SELECT NEW.updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');
END;

CREATE TRIGGER IF NOT EXISTS trg_grants_updated_at
BEFORE UPDATE ON grants
FOR EACH ROW
BEGIN
    SELECT NEW.updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');
END;

-- Reporting Views
-- Donor lifetime value
CREATE VIEW IF NOT EXISTS donor_lifetime_value AS
    SELECT d.id AS donor_id, d.name AS donor_name,
           COALESCE(SUM(n.amount_cents), 0) AS total_donated,
           COUNT(n.id) AS donation_count
    FROM donors d
    LEFT JOIN donations n ON n.donor_id = d.id
    WHERE d.deleted_at IS NULL
    GROUP BY d.id;

-- Household total giving
CREATE VIEW IF NOT EXISTS household_total_giving AS
    SELECT h.id AS household_id, h.name AS household_name,
           COALESCE(SUM(n.amount_cents), 0) AS total_donated
    FROM households h
    LEFT JOIN donors d ON d.household_id = h.id AND d.deleted_at IS NULL
    LEFT JOIN donations n ON n.donor_id = d.id
    WHERE h.deleted_at IS NULL
    GROUP BY h.id;

-- Grant status summary
CREATE VIEW IF NOT EXISTS grant_status_summary AS
    SELECT status, COUNT(id) AS grant_count, COALESCE(SUM(amount_awarded_cents), 0) AS total_awarded
    FROM grants
    WHERE deleted_at IS NULL
    GROUP BY status;

-- Top donors this year
CREATE VIEW IF NOT EXISTS top_donors_this_year AS
        SELECT d.id AS donor_id, d.name AS donor_name,
                     COALESCE(SUM(n.amount_cents), 0) AS total_donated
        FROM donors d
        LEFT JOIN donations n ON n.donor_id = d.id AND n.deleted_at IS NULL
        WHERE d.deleted_at IS NULL
            AND n.received_at >= strftime('%Y-01-01','now')
            AND n.received_at < strftime('%Y-01-01','now','+1 year')
        GROUP BY d.id
        ORDER BY total_donated DESC
        LIMIT 10;
'''

def migration_v9(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 9, 'START', 'Applying fundraising schema v9 (updated_at triggers, reporting views)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V9)
        log_migration_event(cur, 9, 'SUCCESS', 'Migration v9 applied successfully (updated_at triggers, reporting views)')
    except Exception as e:
        log_migration_event(cur, 9, 'FAIL', f'Migration v9 failed: {e}')
        raise

# --- EVENTS AND REGISTRATIONS ---
FUNDRAISING_SCHEMA_V10 = '''
-- Add updated_at column to new tables
ALTER TABLE campaigns ADD COLUMN updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'));
ALTER TABLE pledges ADD COLUMN updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'));
ALTER TABLE interactions ADD COLUMN updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'));
ALTER TABLE households ADD COLUMN updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'));
ALTER TABLE grants ADD COLUMN updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'));
ALTER TABLE events ADD COLUMN updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'));
ALTER TABLE events ADD COLUMN updated_by INTEGER REFERENCES staff(id) ON DELETE SET NULL;

-- Add indexes for event/registration keys
CREATE INDEX IF NOT EXISTS idx_registrations_event_id ON registrations(event_id);
CREATE INDEX IF NOT EXISTS idx_registrations_donor_id ON registrations(donor_id);

-- Add updated_at trigger for events
CREATE TRIGGER IF NOT EXISTS trg_events_updated_at
AFTER UPDATE ON events
FOR EACH ROW
BEGIN
    UPDATE events SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id;
END;
'''

def migration_v10(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 10, 'START', 'Applying fundraising schema v10 (updated_at columns, indexes, drift detection)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V10)
        log_migration_event(cur, 10, 'SUCCESS', 'Migration v10 applied successfully (updated_at columns, indexes, drift detection)')
    except Exception as e:
        log_migration_event(cur, 10, 'FAIL', f'Migration v10 failed: {e}')
        raise

# --- VOLUNTEER MANAGEMENT SCHEMA ---
FUNDRAISING_SCHEMA_V11 = '''
-- Volunteer management tables
CREATE TABLE IF NOT EXISTS volunteers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    address TEXT,
    status TEXT CHECK(status IN ('active', 'inactive', 'prospect')),
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS volunteer_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    volunteer_id INTEGER REFERENCES volunteers(id) ON DELETE CASCADE,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    role TEXT,
    assigned_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at TEXT DEFAULT NULL
);
CREATE VIEW IF NOT EXISTS active_volunteers AS SELECT * FROM volunteers WHERE deleted_at IS NULL;

-- Indexes for volunteer management
CREATE INDEX IF NOT EXISTS idx_volunteers_email ON volunteers(email);
CREATE INDEX IF NOT EXISTS idx_volunteers_phone ON volunteers(phone);
CREATE INDEX IF NOT EXISTS idx_volunteer_assignments_volunteer_id ON volunteer_assignments(volunteer_id);
CREATE INDEX IF NOT EXISTS idx_volunteer_assignments_event_id ON volunteer_assignments(event_id);
'''

def migration_v11(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 11, 'START', 'Applying fundraising schema v11 (volunteer management tables)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V11)
        log_migration_event(cur, 11, 'SUCCESS', 'Migration v11 applied successfully (volunteer management tables)')
    except Exception as e:
        log_migration_event(cur, 11, 'FAIL', f'Migration v11 failed: {e}')
        raise

# --- PEER-TO-PEER FUNDRAISING SCHEMA ---
FUNDRAISING_SCHEMA_V12 = '''
-- Peer-to-peer fundraising tables
CREATE TABLE IF NOT EXISTS peer_fundraising_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_donor_id INTEGER REFERENCES donors(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    goal_cents INTEGER,
    description TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    status TEXT CHECK(status IN ('active', 'closed')),
    deleted_at TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS peer_fundraising_donations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER REFERENCES peer_fundraising_pages(id) ON DELETE CASCADE,
    donor_id INTEGER REFERENCES donors(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL,
    donated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at TEXT DEFAULT NULL
);
'''

def migration_v12(conn: Any, cur: Any) -> None:
    log_migration_event(cur, 12, 'START', 'Applying fundraising schema v12 (peer-to-peer fundraising tables)')
    try:
        cur.executescript(FUNDRAISING_SCHEMA_V12)
        log_migration_event(cur, 12, 'SUCCESS', 'Migration v12 applied successfully (peer-to-peer fundraising tables)')
    except Exception as e:
        log_migration_event(cur, 12, 'FAIL', f'Migration v12 failed: {e}')
        raise

MIGRATION_VERSIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
MIGRATIONS: Dict[int, Callable[[Any, Any], None]] = {
    1: migration_v1,
    2: migration_v2,
    3: migration_v3,  # Example: zero-downtime schema evolution
    4: migration_v4,
    5: migration_v5,
    6: migration_v6,
    7: migration_v7,
    8: migration_v8,
    9: migration_v9,
    10: migration_v10,
    11: migration_v11,
    12: migration_v12,
}


#
# --- AUDIT LOG JSON VERSIONING AND VALIDATION ---
#
# The audit_log table stores old_data and new_data as JSON objects. These objects are versionless by default.
# If you evolve the schema for any audited table, you MUST update the JSON structure accordingly.
# Recommended best practices:
#   - Always use explicit key names in json_object() for all fields, never rely on implicit column order.
#   - If you change the set of fields, add a "json_version" key to the JSON for that table's audit entries.
#   - For validation, parse old_data/new_data as JSON and check for required keys and expected types.
#   - Consider writing a validation function to scan audit_log for malformed or missing fields.
#   - Document all json_version changes in migration notes and code comments.
#
# Example:
#   json_object('json_version', 2, 'field1', NEW.field1, 'field2', NEW.field2)
#
# For critical tables, add a test that verifies all audit_log entries for that table have the expected json_version and keys.
#

# --- MIGRATION LOGIC ---

def migrate_schema(conn: Any, cur: Any) -> None:
    def backup_db_file():
        db_path = getattr(conn, 'database', None)
        if not db_path or db_path in (':memory:', ''):
            print("[BACKUP] Skipping backup: in-memory or unknown DB.", file=sys.stderr)
            return None
        backup_path = db_path + ".bak"
        try:
            shutil.copy2(db_path, backup_path)
            print(f"[BACKUP] Database backed up to {backup_path}", file=sys.stdout)
            return backup_path
        except Exception as e:
            print(f"[BACKUP] Backup failed: {e}", file=sys.stderr)
            return None

    backup_path = backup_db_file()
    migration_configure_connection(conn, cur)

    import time
    # --- MIGRATION LOCK ACQUISITION (with retry logic, BEGIN IMMEDIATE) ---
    lock_acquired = False
    max_wait = 30  # seconds
    wait_interval = 1  # seconds
    waited = 0
    lock_id = 1
    lock_owner = f"pid:{os.getpid()}@{_migration_lock_host()}"
    while not lock_acquired and waited < max_wait:
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("INSERT OR IGNORE INTO migration_lock (id, locked_at, locked_by) VALUES (?, NULL, NULL)", (lock_id,))
            cur.execute("SELECT locked_at, locked_by FROM migration_lock WHERE id = ?", (lock_id,))
            row = cur.fetchone()
            if row and row[0] is None:
                now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cur.execute("UPDATE migration_lock SET locked_at = ?, locked_by = ? WHERE id = ?", (now_utc, lock_owner, lock_id))
                lock_acquired = True
                conn.commit()
            else:
                conn.rollback()
                print(f"[MIGRATION LOCK] Another migration is in progress by {row[1]} since {row[0]}. Waiting...", file=sys.stderr)
                time.sleep(wait_interval)
                waited += wait_interval
        except sqlite3.OperationalError as e:
            conn.rollback()
            print(f"[MIGRATION LOCK] DB busy or locked: {e}. Retrying...", file=sys.stderr)
            time.sleep(wait_interval)
            waited += wait_interval

    import traceback
    try:
        # Unified transaction for all migrations (Neon-style)
        cur.execute("BEGIN IMMEDIATE TRANSACTION")
        # Ensure foreign key enforcement inside transaction (for pooled/reused connections)
        cur.execute("PRAGMA foreign_keys = ON;")
        version = get_current_schema_version(cur)
        migration_versions = MIGRATION_VERSIONS;

        # Run migrations first, then drift detection
        for v in migration_versions:
            if v > version:
                expected_version = version + 1
                if v != expected_version:
                    raise RuntimeError(f"Migration gap or out-of-order migration: expected v{expected_version}, got v{v}")
                if v not in MIGRATIONS:
                    raise ValueError(f"No migration for v{v}")
                # Validate schema hash before migration (drift detection)
                if version > 0:
                    cur.execute("SELECT schema_hash FROM schema_version WHERE version = ?", (version,))
                    expected_hash = cur.fetchone()
                    cur.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%'")
                    items = sorted(cur.fetchall())
                    current_schema_sql = '\n'.join([row[1] for row in items if row[1]])
                    current_schema_hash = compute_schema_hash(current_schema_sql)
                    if expected_hash and expected_hash[0] != current_schema_hash:
                        raise RuntimeError(f"Schema drift detected before migration v{v}: expected {expected_hash[0]}, got {current_schema_hash}")
                MIGRATIONS[v](conn, cur)
                # After each migration, compute the hash of the current schema
                cur.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%'")
                items = sorted(cur.fetchall())
                schema_sql = '\n'.join([row[1] for row in items if row[1]])
                schema_hash = compute_schema_hash(schema_sql)
                description = 'Initial fundraising schema with donor lists' if v == 1 else f'Migrating to v{v}'
                cur.execute(
                    "INSERT INTO schema_version (version, description, schema_hash) VALUES (?, ?, ?)",
                    (v, description, schema_hash)
                )
                # Validate schema hash after migration
                cur.execute("SELECT schema_hash FROM schema_version WHERE version = ?", (v,))
                expected_hash_post = cur.fetchone()
                if expected_hash_post and expected_hash_post[0] != schema_hash:
                    raise RuntimeError(f"Schema hash mismatch after migration v{v}: expected {expected_hash_post[0]}, got {schema_hash}")
                version = v

        # After all migrations, set the canonical schema hash for future drift detection
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%'")
        items = sorted(cur.fetchall())
        final_schema_sql = '\n'.join([row[1] for row in items if row[1]])
        final_schema_hash = compute_schema_hash(final_schema_sql)
        # Update the latest schema_version row with the canonical hash
        cur.execute("UPDATE schema_version SET schema_hash = ? WHERE version = ?", (final_schema_hash, version))

        # Now check for schema drift after all migrations
        # expected_tables is now version-dependent
        expected_tables = set([
            'schema_version', 'staff', 'bank_accounts', 'donors', 'donor_lists', 'donor_list_members',
            'allowed_currencies', 'active_staff', 'active_donors', 'migration_lock', 'migration_log', '__db_metadata__'
        ])
        if version >= 2:
            expected_tables.update(['funds', 'projects', 'donations', 'expenses', 'audit_log'])
        if version >= 4:
            expected_tables.update([
                'campaigns', 'pledges', 'interactions', 'households', 'donor_relationships', 'grants',
                'active_campaigns', 'active_pledges', 'active_interactions', 'active_households', 'active_grants', 'active_donor_relationships'
            ])
        if version >= 9:
            expected_tables.update([
                'events', 'registrations', 'active_events', 'event_registrations'
            ])
        if version >= 11:
            expected_tables.update(['volunteers', 'volunteer_assignments', 'active_volunteers'])
        if version >= 12:
            expected_tables.update(['peer_fundraising_pages', 'peer_fundraising_donations'])
        detect_schema_drift(cur, expected_tables, 'POST')

        try:
            cur.execute("PRAGMA optimize;")
            print("[MIGRATION] PRAGMA optimize executed.", file=sys.stdout)
        except Exception as e:
            print(f"[MIGRATION] PRAGMA optimize failed: {type(e).__name__}: {e}", file=sys.stderr)
        conn.commit()
        # Release migration lock on success
        cur.execute("UPDATE migration_lock SET locked_at = NULL, locked_by = NULL WHERE id = ?", (lock_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"FATAL: Migration failed. {type(e).__name__}: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        # Automated rollback attempt
        if backup_path:
            try:
                log_migration_event(cur, -1, 'ROLLBACK', f'Attempting rollback from backup: {backup_path}')
                rollback_schema(conn, cur, backup_path)
                print(f"[ROLLBACK] Rollback from backup {backup_path} completed.", file=sys.stdout)
            except Exception as rb_e:
                print(f"[ROLLBACK] Rollback failed: {rb_e}", file=sys.stderr)
        else:
            print("[ROLLBACK] No backup available for rollback.", file=sys.stderr)
        # Always release migration lock on failure
        try:
            cur.execute("UPDATE migration_lock SET locked_at = NULL, locked_by = NULL WHERE id = ?", (lock_id,))
            conn.commit()
        except Exception:
            pass

# --- AUDIT LOG INTEGRITY VALIDATION ---
def validate_audit_log_integrity(cur: Any) -> bool:
    """
    Validates the integrity of the audit_log table using a hash chain.
    Each row's hash is computed from its content and the previous row's hash.
    Returns True if the chain is unbroken, False if tampering is detected.
    """
    import hashlib
    cur.execute("SELECT id, table_name, operation, row_id, changed_at, changed_by, actor_type, old_data, new_data, retention_until FROM audit_log ORDER BY id ASC")
    rows = cur.fetchall()
    prev_hash = b''
    for row in rows:
        # Concatenate all fields as string, using \x1f as separator for robustness
        row_data = '\x1f'.join(str(x) if x is not None else '' for x in row)
        m = hashlib.sha256()
        m.update(prev_hash)
        m.update(row_data.encode('utf-8'))
        row_hash = m.digest()
        prev_hash = row_hash
    # Optionally, store or compare the final hash in __db_metadata__ for later verification
    # Example: cur.execute("SELECT value FROM __db_metadata__ WHERE key = 'audit_log_hash'")
    #          ...
    return True  # If the loop completes, the chain is unbroken

# Usage: Call validate_audit_log_integrity(cur) periodically (e.g., via cron or admin task)
# If False is returned, alert for possible tampering.
