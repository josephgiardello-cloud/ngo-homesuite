from __future__ import annotations

import sqlite3

from ..config import DB_PATH
from .connection import configure_connection, connect_db_at

SCHEMA_VERSION = 2

# Version 0 is the unversioned CLI schema (including its additive predecessors).
TABLES = {
    "app_meta": "key TEXT PRIMARY KEY, value TEXT",
    "users": """id INTEGER PRIMARY KEY, username TEXT UNIQUE,
        password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
        role TEXT NOT NULL, created_at TEXT NOT NULL""",
    "audit_log": """id INTEGER PRIMARY KEY, at_utc TEXT NOT NULL, user_id INTEGER,
        username TEXT CHECK(username IS NULL OR LENGTH(username) <= 100),
        role TEXT CHECK(role IS NULL OR LENGTH(role) <= 30),
        action TEXT NOT NULL CHECK(LENGTH(action) <= 80),
        entity_type TEXT CHECK(entity_type IS NULL OR LENGTH(entity_type) <= 50),
        entity_id INTEGER, details_json TEXT""",
    "donors": "id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, address TEXT",
    "campaigns": """id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        start_date TEXT, end_date TEXT, goal_amount REAL, active INTEGER NOT NULL,
        notes TEXT CHECK(notes IS NULL OR LENGTH(notes) <= 2000),
        created_at TEXT NOT NULL, created_by_user_id INTEGER REFERENCES users(id),
        CHECK(LENGTH(name) <= 120)""",
    "pledges": """id INTEGER PRIMARY KEY, donor_id INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
        campaign_id INTEGER REFERENCES campaigns(id), amount REAL NOT NULL,
        frequency TEXT NOT NULL CHECK(LENGTH(frequency) <= 20), start_date TEXT NOT NULL,
        end_date TEXT, active INTEGER NOT NULL, notes TEXT CHECK(notes IS NULL OR LENGTH(notes) <= 2000),
        created_at TEXT NOT NULL, created_by_user_id INTEGER REFERENCES users(id)""",
    "donations": """id INTEGER PRIMARY KEY, donor_id INTEGER REFERENCES donors(id),
        campaign_id INTEGER REFERENCES campaigns(id), pledge_id INTEGER REFERENCES pledges(id),
        amount REAL, date TEXT, purpose TEXT""",
    "donation_allocations": """id INTEGER PRIMARY KEY,
        donation_id INTEGER NOT NULL REFERENCES donations(id) ON DELETE CASCADE,
        project_id INTEGER REFERENCES projects(id), category TEXT NOT NULL, amount REAL NOT NULL""",
    "volunteers": "id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, skills TEXT, availability TEXT",
    "projects": "id INTEGER PRIMARY KEY, name TEXT, description TEXT, budget REAL, spent REAL, start_date TEXT",
    "donor_tags": """id INTEGER PRIMARY KEY, donor_id INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
        tag TEXT NOT NULL CHECK(LENGTH(tag) <= 50), created_at TEXT NOT NULL,
        created_by_user_id INTEGER REFERENCES users(id), UNIQUE(donor_id, tag)""",
    "donor_interactions": """id INTEGER PRIMARY KEY,
        donor_id INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
        occurred_at TEXT NOT NULL, channel TEXT NOT NULL CHECK(LENGTH(channel) <= 20),
        summary TEXT NOT NULL CHECK(LENGTH(summary) <= 500),
        next_action TEXT CHECK(next_action IS NULL OR LENGTH(next_action) <= 250),
        follow_up_due TEXT, completed_at TEXT, created_at TEXT NOT NULL,
        created_by_user_id INTEGER REFERENCES users(id)""",
    "staff": """id INTEGER PRIMARY KEY, name TEXT NOT NULL, title TEXT, jurisdiction_code TEXT,
        pay_type TEXT NOT NULL, pay_frequency TEXT NOT NULL, hourly_rate REAL, annual_salary REAL,
        overtime_multiplier REAL, tax_withholding_pct REAL, other_withholding_pct REAL,
        other_withholding_fixed REAL, employer_tax_pct REAL, employer_other_pct REAL,
        employer_other_fixed REAL, active INTEGER NOT NULL, start_date TEXT, end_date TEXT, notes TEXT""",
    "payroll_runs": """id INTEGER PRIMARY KEY, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
        run_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'committed', paid_at TEXT,
        created_by_user_id INTEGER REFERENCES users(id)""",
    "payroll_items": """id INTEGER PRIMARY KEY,
        payroll_run_id INTEGER NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
        staff_id INTEGER NOT NULL REFERENCES staff(id), jurisdiction_code TEXT,
        hours_regular REAL, hours_overtime REAL, gross REAL NOT NULL,
        tax_withheld REAL NOT NULL, other_withheld REAL NOT NULL, net REAL NOT NULL,
        employer_tax REAL NOT NULL, employer_other REAL NOT NULL,
        employer_total REAL NOT NULL, total_cost REAL NOT NULL""",
}

LEGACY_ADDITIONS = {
    "donations": {"campaign_id": "INTEGER", "pledge_id": "INTEGER"},
    "staff": {
        "jurisdiction_code": "TEXT", "employer_tax_pct": "REAL",
        "employer_other_pct": "REAL", "employer_other_fixed": "REAL",
    },
    "payroll_items": {
        "jurisdiction_code": "TEXT", "employer_tax": "REAL", "employer_other": "REAL",
        "employer_total": "REAL", "total_cost": "REAL",
    },
    "payroll_runs": {"status": "TEXT", "paid_at": "TEXT"},
}


def _legacy(conn: sqlite3.Connection) -> None:
    for table, definition in TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({definition})")
    for table, additions in LEGACY_ADDITIONS.items():
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, sql_type in additions.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
    conn.execute("UPDATE payroll_runs SET status = 'paid' WHERE status IS NULL OR TRIM(status) = ''")
    conn.execute("UPDATE staff SET jurisdiction_code = 'US-ME' WHERE jurisdiction_code IS NULL OR TRIM(jurisdiction_code) = ''")
    conn.execute("""UPDATE staff SET employer_tax_pct = COALESCE(employer_tax_pct, 0),
        employer_other_pct = COALESCE(employer_other_pct, 0),
        employer_other_fixed = COALESCE(employer_other_fixed, 0)""")
    conn.execute("UPDATE donors SET email = NULL WHERE email IS NOT NULL AND TRIM(email) = ''")
    conn.execute("UPDATE projects SET spent = 0 WHERE spent IS NULL")
    for table, columns in {
        "donations": ("donor_id", "campaign_id", "pledge_id"),
        "donation_allocations": ("donation_id", "project_id"),
        "payroll_items": ("payroll_run_id", "staff_id"),
        "staff": ("active",), "campaigns": ("active",),
        "pledges": ("donor_id", "campaign_id", "active"),
        "donor_tags": ("donor_id", "tag"),
        "donor_interactions": ("donor_id", "follow_up_due", "completed_at"),
        "audit_log": ("at_utc", "user_id", "action"),
    }.items():
        for column in columns:
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{column} ON {table}({column})")
    # Conflicts abort the migration; operators must resolve them rather than run a partial schema.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_donors_email_unique_v2 ON donors(email)")


def _services(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE donations ADD COLUMN idempotency_key TEXT")
    conn.execute("ALTER TABLE donations ADD COLUMN request_hash TEXT")
    conn.execute("CREATE UNIQUE INDEX donation_idempotency ON donations(idempotency_key)")
    conn.execute("""CREATE TABLE grants (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
        name TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
        status TEXT NOT NULL CHECK(status IN ('draft', 'approved', 'paid', 'cancelled')),
        created_by INTEGER NOT NULL REFERENCES users(id), approved_by INTEGER REFERENCES users(id),
        paid_by INTEGER REFERENCES users(id),
        CHECK(status NOT IN ('approved', 'paid') OR approved_by IS NOT NULL),
        CHECK(status != 'paid' OR (paid_by IS NOT NULL AND paid_by != approved_by)))""")
    conn.execute("CREATE INDEX grants_project_status ON grants(project_id, status)")
    conn.execute("""CREATE TABLE outbox (
        id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL UNIQUE REFERENCES audit_log(id),
        payload TEXT NOT NULL, delivered_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT)""")
    conn.execute("""CREATE TABLE web_sessions (
        token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
        csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE login_attempts (
        username TEXT PRIMARY KEY, failures INTEGER NOT NULL, retry_after INTEGER NOT NULL)""")


def migrate(conn: sqlite3.Connection) -> None:
    """Atomically install/upgrade versions 0 and 1; reject future schemas."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
        version = int(row[0]) if row else 0
        if version < 0 or version > SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema version: {version}")
        for target, migration in ((1, _legacy), (2, _services)):
            if version < target:
                migration(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('schema_version', ?)",
                    (str(target),),
                )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def init_db(path: str | None = None) -> None:
    conn = connect_db_at(path or DB_PATH)
    try:
        configure_connection(conn)
        migrate(conn)
    finally:
        conn.close()
