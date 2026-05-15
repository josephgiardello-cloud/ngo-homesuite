-- Migration 0002: Reporting SQL views

CREATE VIEW IF NOT EXISTS donor_summaries AS
SELECT d.id AS donor_id, d.name, COUNT(n.id) AS donation_count, SUM(n.amount) AS total_donated
FROM donors d
LEFT JOIN donations n ON d.id = n.donor_id
GROUP BY d.id, d.name;

CREATE VIEW IF NOT EXISTS fund_balances AS
SELECT f.id AS fund_id, f.name, COALESCE(SUM(n.amount), 0) AS balance
FROM funds f
LEFT JOIN donations n ON f.id = n.fund_id
GROUP BY f.id, f.name;

CREATE VIEW IF NOT EXISTS project_financials AS
SELECT p.id AS project_id, p.name, COALESCE(SUM(n.amount), 0) AS total_income, COALESCE(SUM(e.amount), 0) AS total_expense
FROM projects p
LEFT JOIN donations n ON p.id = n.project_id
LEFT JOIN expenses e ON p.id = e.project_id
GROUP BY p.id, p.name;

CREATE VIEW IF NOT EXISTS expense_breakdowns AS
SELECT e.id AS expense_id, e.category, e.amount, e.project_id, e.fund_id, e.date
FROM expenses e;
