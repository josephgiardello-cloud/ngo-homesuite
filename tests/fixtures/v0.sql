-- Unversioned predecessor: before campaigns/pledges and employer payroll fields.
CREATE TABLE donors (id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, address TEXT);
CREATE TABLE donations (id INTEGER PRIMARY KEY, donor_id INTEGER REFERENCES donors(id), amount REAL, date TEXT, purpose TEXT);
CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, description TEXT, budget REAL, spent REAL, start_date TEXT);
CREATE TABLE staff (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, title TEXT, pay_type TEXT NOT NULL,
 pay_frequency TEXT NOT NULL, hourly_rate REAL, annual_salary REAL, overtime_multiplier REAL,
 tax_withholding_pct REAL, other_withholding_pct REAL, other_withholding_fixed REAL,
 active INTEGER NOT NULL, start_date TEXT, end_date TEXT, notes TEXT);
CREATE TABLE payroll_runs (id INTEGER PRIMARY KEY, period_start TEXT NOT NULL, period_end TEXT NOT NULL, run_at TEXT NOT NULL);
CREATE TABLE payroll_items (
 id INTEGER PRIMARY KEY, payroll_run_id INTEGER NOT NULL REFERENCES payroll_runs(id),
 staff_id INTEGER NOT NULL REFERENCES staff(id), hours_regular REAL, hours_overtime REAL,
 gross REAL NOT NULL, tax_withheld REAL NOT NULL, other_withheld REAL NOT NULL, net REAL NOT NULL);
INSERT INTO donors VALUES (1, 'Historical donor', 'historic@example.org', NULL, NULL);
INSERT INTO donations VALUES (1, 1, 12.34, '2025-01-01', 'Historical receipt');
INSERT INTO projects VALUES (1, 'Historical project', '', 1000, NULL, '2025-01-01');
INSERT INTO staff(id, name, pay_type, pay_frequency, active) VALUES (1, 'Historical staff', 'hourly', 'monthly', 1);
INSERT INTO payroll_runs VALUES (1, '2025-01-01', '2025-01-31', '2025-02-01');
INSERT INTO payroll_items(id, payroll_run_id, staff_id, gross, tax_withheld, other_withheld, net)
 VALUES (1, 1, 1, 100, 10, 0, 90);
