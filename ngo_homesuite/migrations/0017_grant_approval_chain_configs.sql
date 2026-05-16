-- Migration 0017: add persistent per-organization approval chain configuration

CREATE TABLE IF NOT EXISTS grant_approval_chain_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    action_type VARCHAR(60) NOT NULL,
    min_amount REAL,
    max_amount REAL,
    required_approvals INTEGER NOT NULL DEFAULT 1,
    approver_roles_json TEXT NOT NULL,
    escalation_role VARCHAR(40),
    sla_hours INTEGER NOT NULL DEFAULT 72,
    escalation_sla_hours INTEGER NOT NULL DEFAULT 24,
    priority INTEGER NOT NULL DEFAULT 100,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE INDEX IF NOT EXISTS idx_grant_approval_chain_configs_org ON grant_approval_chain_configs(organization_id);
CREATE INDEX IF NOT EXISTS idx_grant_approval_chain_configs_action ON grant_approval_chain_configs(action_type);
CREATE INDEX IF NOT EXISTS idx_grant_approval_chain_configs_priority ON grant_approval_chain_configs(priority);
CREATE INDEX IF NOT EXISTS idx_grant_approval_chain_configs_active ON grant_approval_chain_configs(is_active);
