-- Migration 0016: support configurable approval chains, expiration/escalation, and rationale fields

ALTER TABLE grant_approval_requests ADD COLUMN required_approvals INTEGER NOT NULL DEFAULT 1;
ALTER TABLE grant_approval_requests ADD COLUMN approver_roles_json TEXT;
ALTER TABLE grant_approval_requests ADD COLUMN expires_at DATETIME;
ALTER TABLE grant_approval_requests ADD COLUMN escalated_at DATETIME;
ALTER TABLE grant_approval_requests ADD COLUMN escalation_role VARCHAR(40);

CREATE INDEX IF NOT EXISTS idx_grant_approval_requests_expires_at ON grant_approval_requests(expires_at);

ALTER TABLE grant_approval_decisions ADD COLUMN rationale TEXT;
