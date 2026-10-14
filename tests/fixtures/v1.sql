-- Version 1 release contract: complete CLI schema before transactional services.
CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO app_meta VALUES ('schema_version', '1');
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_salt TEXT NOT NULL,
 password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY, at_utc TEXT NOT NULL, user_id INTEGER, username TEXT,
 role TEXT, action TEXT NOT NULL, entity_type TEXT, entity_id INTEGER, details_json TEXT);
CREATE TABLE donors (id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, address TEXT);
CREATE TABLE campaigns (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, start_date TEXT,
 end_date TEXT, goal_amount REAL, active INTEGER NOT NULL, notes TEXT, created_at TEXT NOT NULL,
 created_by_user_id INTEGER REFERENCES users(id));
CREATE TABLE pledges (id INTEGER PRIMARY KEY, donor_id INTEGER NOT NULL REFERENCES donors(id),
 campaign_id INTEGER REFERENCES campaigns(id), amount REAL NOT NULL, frequency TEXT NOT NULL,
 start_date TEXT NOT NULL, end_date TEXT, active INTEGER NOT NULL, notes TEXT,
 created_at TEXT NOT NULL, created_by_user_id INTEGER REFERENCES users(id));
CREATE TABLE donations (id INTEGER PRIMARY KEY, donor_id INTEGER REFERENCES donors(id),
 campaign_id INTEGER REFERENCES campaigns(id), pledge_id INTEGER REFERENCES pledges(id),
 amount REAL, date TEXT, purpose TEXT);
CREATE TABLE donation_allocations (id INTEGER PRIMARY KEY,
 donation_id INTEGER NOT NULL REFERENCES donations(id) ON DELETE CASCADE,
 project_id INTEGER REFERENCES projects(id), category TEXT NOT NULL, amount REAL NOT NULL);
CREATE TABLE volunteers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, skills TEXT, availability TEXT);
CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, description TEXT, budget REAL, spent REAL, start_date TEXT);
CREATE TABLE donor_tags (id INTEGER PRIMARY KEY, donor_id INTEGER NOT NULL REFERENCES donors(id),
 tag TEXT NOT NULL, created_at TEXT NOT NULL, created_by_user_id INTEGER REFERENCES users(id), UNIQUE(donor_id, tag));
CREATE TABLE donor_interactions (id INTEGER PRIMARY KEY, donor_id INTEGER NOT NULL REFERENCES donors(id),
 occurred_at TEXT NOT NULL, channel TEXT NOT NULL, summary TEXT NOT NULL, next_action TEXT,
 follow_up_due TEXT, completed_at TEXT, created_at TEXT NOT NULL, created_by_user_id INTEGER REFERENCES users(id));
CREATE TABLE staff (id INTEGER PRIMARY KEY, name TEXT NOT NULL, title TEXT, jurisdiction_code TEXT,
 pay_type TEXT NOT NULL, pay_frequency TEXT NOT NULL, hourly_rate REAL, annual_salary REAL,
 overtime_multiplier REAL, tax_withholding_pct REAL, other_withholding_pct REAL, other_withholding_fixed REAL,
 employer_tax_pct REAL, employer_other_pct REAL, employer_other_fixed REAL, active INTEGER NOT NULL,
 start_date TEXT, end_date TEXT, notes TEXT);
CREATE TABLE payroll_runs (id INTEGER PRIMARY KEY, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
 run_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'committed', paid_at TEXT,
 created_by_user_id INTEGER REFERENCES users(id));
CREATE TABLE payroll_items (id INTEGER PRIMARY KEY, payroll_run_id INTEGER NOT NULL REFERENCES payroll_runs(id),
 staff_id INTEGER NOT NULL REFERENCES staff(id), jurisdiction_code TEXT, hours_regular REAL, hours_overtime REAL,
 gross REAL NOT NULL, tax_withheld REAL NOT NULL, other_withheld REAL NOT NULL, net REAL NOT NULL,
 employer_tax REAL NOT NULL, employer_other REAL NOT NULL, employer_total REAL NOT NULL, total_cost REAL NOT NULL);
CREATE UNIQUE INDEX idx_donors_email_unique_v2 ON donors(email);
INSERT INTO donors VALUES (1, 'Historical donor', 'historic@example.org', NULL, NULL);
INSERT INTO donations VALUES (1, 1, NULL, NULL, 12.34, '2025-01-01', 'Historical receipt');
INSERT INTO projects VALUES (1, 'Historical project', '', 1000, 0, '2025-01-01');
