-- Migration 0015: add grant approval workflow tables

CREATE TABLE IF NOT EXISTS grant_approval_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    action_type VARCHAR(60) NOT NULL,
    resource_type VARCHAR(40) NOT NULL,
    resource_id INTEGER NOT NULL,
    requested_by_user_id INTEGER NOT NULL,
    requested_by_role VARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    payload_json TEXT,
    version_id INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE INDEX IF NOT EXISTS idx_grant_approval_requests_org ON grant_approval_requests(organization_id);
CREATE INDEX IF NOT EXISTS idx_grant_approval_requests_action ON grant_approval_requests(action_type);
CREATE INDEX IF NOT EXISTS idx_grant_approval_requests_resource_type ON grant_approval_requests(resource_type);
CREATE INDEX IF NOT EXISTS idx_grant_approval_requests_resource_id ON grant_approval_requests(resource_id);
CREATE INDEX IF NOT EXISTS idx_grant_approval_requests_requested_by_user_id ON grant_approval_requests(requested_by_user_id);
CREATE INDEX IF NOT EXISTS idx_grant_approval_requests_status ON grant_approval_requests(status);

CREATE TABLE IF NOT EXISTS grant_approval_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    organization_id INTEGER NOT NULL,
    decided_by_user_id INTEGER NOT NULL,
    decided_by_role VARCHAR(40) NOT NULL,
    decision VARCHAR(20) NOT NULL,
    comment TEXT,
    decided_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES grant_approval_requests(id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE INDEX IF NOT EXISTS idx_grant_approval_decisions_request_id ON grant_approval_decisions(request_id);
CREATE INDEX IF NOT EXISTS idx_grant_approval_decisions_org ON grant_approval_decisions(organization_id);
CREATE INDEX IF NOT EXISTS idx_grant_approval_decisions_decided_by_user_id ON grant_approval_decisions(decided_by_user_id);
