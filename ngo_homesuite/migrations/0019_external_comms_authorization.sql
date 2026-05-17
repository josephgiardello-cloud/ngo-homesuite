-- 0019_external_comms_authorization.sql
-- Add explicit user-level authorization controls and immutable authorization records

ALTER TABLE users ADD COLUMN can_authorize_external_comms INTEGER NOT NULL DEFAULT 0;

UPDATE users
SET can_authorize_external_comms = 1
WHERE lower(role) = 'admin';

CREATE TABLE IF NOT EXISTS external_communication_authorizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    user_role TEXT NOT NULL,
    channel TEXT NOT NULL,
    communication_type TEXT NOT NULL,
    campaign_id INTEGER,
    batch_id INTEGER,
    warning_acknowledged INTEGER NOT NULL DEFAULT 0,
    confirmation_phrase TEXT NOT NULL,
    reviewer_name TEXT NOT NULL,
    reviewer_role TEXT,
    details_json TEXT,
    authorized_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY (batch_id) REFERENCES campaign_email_batches(id)
);

CREATE INDEX IF NOT EXISTS idx_external_comm_auth_org ON external_communication_authorizations(organization_id);
CREATE INDEX IF NOT EXISTS idx_external_comm_auth_user ON external_communication_authorizations(user_id);
CREATE INDEX IF NOT EXISTS idx_external_comm_auth_campaign ON external_communication_authorizations(campaign_id);
CREATE INDEX IF NOT EXISTS idx_external_comm_auth_batch ON external_communication_authorizations(batch_id);
CREATE INDEX IF NOT EXISTS idx_external_comm_auth_authorized_at ON external_communication_authorizations(authorized_at);
