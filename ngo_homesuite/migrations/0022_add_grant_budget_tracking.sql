-- Grant budget line-item tracking for restricted fund accounting
-- Enables per-category budget allocation, commitment, and spending tracking

CREATE TABLE IF NOT EXISTS grant_budget_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_id INTEGER NOT NULL,
    organization_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    line_name TEXT NOT NULL,
    allocated_amount REAL NOT NULL,
    notes TEXT,
    version_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (grant_id) REFERENCES grants(id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    UNIQUE (grant_id, category)
);

CREATE INDEX IF NOT EXISTS idx_grant_budget_lines_grant_id ON grant_budget_lines(grant_id);
CREATE INDEX IF NOT EXISTS idx_grant_budget_lines_org_id ON grant_budget_lines(organization_id);
CREATE INDEX IF NOT EXISTS idx_grant_budget_lines_category ON grant_budget_lines(category);

-- Grant expense allocations: track which expenses are charged to which grant budget lines
CREATE TABLE IF NOT EXISTS grant_expense_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_id INTEGER NOT NULL,
    budget_line_id INTEGER NOT NULL,
    expense_id INTEGER NOT NULL UNIQUE,
    organization_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    supporting_document_ref TEXT,
    version_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (grant_id) REFERENCES grants(id),
    FOREIGN KEY (budget_line_id) REFERENCES grant_budget_lines(id),
    FOREIGN KEY (expense_id) REFERENCES expenses(id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE INDEX IF NOT EXISTS idx_grant_expense_allocations_grant_id ON grant_expense_allocations(grant_id);
CREATE INDEX IF NOT EXISTS idx_grant_expense_allocations_budget_line_id ON grant_expense_allocations(budget_line_id);
CREATE INDEX IF NOT EXISTS idx_grant_expense_allocations_expense_id ON grant_expense_allocations(expense_id);
