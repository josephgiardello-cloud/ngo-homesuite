-- Migration 0028: add open/click tracking fields to campaign email deliveries

ALTER TABLE campaign_email_deliveries ADD COLUMN open_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE campaign_email_deliveries ADD COLUMN click_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE campaign_email_deliveries ADD COLUMN last_opened_at DATETIME;
ALTER TABLE campaign_email_deliveries ADD COLUMN last_clicked_at DATETIME;

CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_open_count
    ON campaign_email_deliveries(open_count);
CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_click_count
    ON campaign_email_deliveries(click_count);
CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_last_opened_at
    ON campaign_email_deliveries(last_opened_at);
CREATE INDEX IF NOT EXISTS idx_campaign_email_deliveries_last_clicked_at
    ON campaign_email_deliveries(last_clicked_at);
