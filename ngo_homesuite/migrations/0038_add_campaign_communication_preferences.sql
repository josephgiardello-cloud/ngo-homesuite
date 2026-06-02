-- 0038_add_campaign_communication_preferences.sql
-- Adds a reusable communication preference center for campaign/newsletter channels.

CREATE TABLE IF NOT EXISTS campaign_communication_preferences (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    donor_id INTEGER,
    email TEXT NOT NULL,
    newsletter_opt_in INTEGER NOT NULL DEFAULT 1,
    campaign_opt_in INTEGER NOT NULL DEFAULT 1,
    events_opt_in INTEGER NOT NULL DEFAULT 1,
    volunteer_opt_in INTEGER NOT NULL DEFAULT 1,
    digest_frequency TEXT NOT NULL DEFAULT 'weekly',
    source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (donor_id) REFERENCES donors(id),
    UNIQUE (organization_id, email)
);

CREATE INDEX IF NOT EXISTS idx_campaign_comm_prefs_org ON campaign_communication_preferences(organization_id);
CREATE INDEX IF NOT EXISTS idx_campaign_comm_prefs_email ON campaign_communication_preferences(email);
CREATE INDEX IF NOT EXISTS idx_campaign_comm_prefs_donor ON campaign_communication_preferences(donor_id);

CREATE TRIGGER IF NOT EXISTS trg_campaign_comm_prefs_updated_at
AFTER UPDATE ON campaign_communication_preferences
FOR EACH ROW
BEGIN
    UPDATE campaign_communication_preferences
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
