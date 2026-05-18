-- Migration 0026: add campaign email batch and delivery tables

CREATE TABLE IF NOT EXISTS campaign_email_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    created_by_user_id INTEGER REFERENCES users(id),
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    audience_json TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    total_recipients INTEGER NOT NULL DEFAULT 0,
    sent_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_campaign_email_batches_org ON campaign_email_batches(organization_id);
CREATE INDEX IF NOT EXISTS idx_campaign_email_batches_campaign ON campaign_email_batches(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_email_batches_status ON campaign_email_batches(status);
CREATE INDEX IF NOT EXISTS idx_campaign_email_batches_created_by ON campaign_email_batches(created_by_user_id);

CREATE TABLE IF NOT EXISTS campaign_email_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES campaign_email_batches(id),
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    donor_id INTEGER REFERENCES donors(id),
    recipient_email TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_batch ON campaign_email_deliveries(batch_id);
CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_org ON campaign_email_deliveries(organization_id);
CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_campaign ON campaign_email_deliveries(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_donor ON campaign_email_deliveries(donor_id);
CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_status ON campaign_email_deliveries(delivery_status);
