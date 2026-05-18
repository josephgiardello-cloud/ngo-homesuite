-- Migration 0025: add reconciliation metadata to grant budget transactions

ALTER TABLE grant_budget_transactions ADD COLUMN reconciled_at DATETIME;
ALTER TABLE grant_budget_transactions ADD COLUMN reconciled_by_user_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_budget_transactions_reconciled_at
    ON grant_budget_transactions(reconciled_at);
CREATE INDEX IF NOT EXISTS idx_budget_transactions_reconciled_by
    ON grant_budget_transactions(reconciled_by_user_id);
