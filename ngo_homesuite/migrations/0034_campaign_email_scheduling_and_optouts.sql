-- Migration 0034: campaign email scheduling and opt-out (unsubscribe) support

-- Add scheduled send support to campaign_email_batches
ALTER TABLE campaign_email_batches ADD COLUMN scheduled_at DATETIME;
CREATE INDEX IF NOT EXISTS idx_campaign_email_batches_scheduled ON campaign_email_batches(scheduled_at);

-- Create email opt-out / unsubscribe table
CREATE TABLE IF NOT EXISTS campaign_email_opt_outs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    donor_id INTEGER REFERENCES donors(id),
    email TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    campaign_id INTEGER REFERENCES campaigns(id),
    unsubscribed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_email_opt_outs_org ON campaign_email_opt_outs(organization_id);
CREATE INDEX IF NOT EXISTS idx_email_opt_outs_email ON campaign_email_opt_outs(email);
CREATE INDEX IF NOT EXISTS idx_email_opt_outs_token ON campaign_email_opt_outs(token);
