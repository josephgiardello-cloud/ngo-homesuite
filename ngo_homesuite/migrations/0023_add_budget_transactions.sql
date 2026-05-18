-- Migration 0023: Add budget commitment tracking to grant budget lines
-- Adds status, committed_amount, and reconciled_amount to grant_budget_lines
-- Creates grant_budget_transactions table for transaction history

-- Add new columns to grant_budget_lines
ALTER TABLE grant_budget_lines ADD COLUMN status TEXT DEFAULT 'pending' NOT NULL;
ALTER TABLE grant_budget_lines ADD COLUMN committed_amount REAL DEFAULT 0.0 NOT NULL;
ALTER TABLE grant_budget_lines ADD COLUMN reconciled_amount REAL DEFAULT 0.0 NOT NULL;

-- Create grant_budget_transactions table for transaction audit trail
CREATE TABLE grant_budget_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_line_id INTEGER NOT NULL REFERENCES grant_budget_lines(id),
    grant_id INTEGER NOT NULL REFERENCES grants(id),
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    transaction_type TEXT NOT NULL, -- commit, reconcile, reverse, adjust
    amount REAL NOT NULL,
    description TEXT,
    reference_type TEXT, -- expense, invoice, manual
    reference_id INTEGER,
    created_by_user_id INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(budget_line_id, id)
);

-- Create indexes for optimal query performance
CREATE INDEX idx_budget_transactions_budget_line ON grant_budget_transactions(budget_line_id);
CREATE INDEX idx_budget_transactions_grant ON grant_budget_transactions(grant_id);
CREATE INDEX idx_budget_transactions_org ON grant_budget_transactions(organization_id);
CREATE INDEX idx_budget_transactions_type ON grant_budget_transactions(transaction_type);
CREATE INDEX idx_budget_transactions_created ON grant_budget_transactions(created_at);
