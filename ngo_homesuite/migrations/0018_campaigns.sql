-- Migration 0018: Campaign management
-- Adds campaigns table and campaign_id FK to p2p_pages

CREATE TABLE IF NOT EXISTS campaigns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    fund_id         INTEGER REFERENCES funds(id) ON DELETE SET NULL,
    name            TEXT    NOT NULL,
    slug            TEXT    NOT NULL,
    description     TEXT,
    campaign_type   TEXT    NOT NULL DEFAULT 'general'
                    CHECK (campaign_type IN ('annual','capital','event','emergency','recurring','p2p','general')),
    status          TEXT    NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','active','paused','closed')),
    goal_amount     REAL    NOT NULL DEFAULT 0.0,
    raised_amount   REAL    NOT NULL DEFAULT 0.0,
    currency        TEXT    NOT NULL DEFAULT 'USD',
    start_date      DATE,
    end_date        DATE,
    notes           TEXT,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_campaigns_org_slug
    ON campaigns(organization_id, slug);
CREATE INDEX IF NOT EXISTS ix_campaigns_organization_id ON campaigns(organization_id);
CREATE INDEX IF NOT EXISTS ix_campaigns_status ON campaigns(status);

-- Add campaign_id FK to p2p_pages (nullable for existing rows)
ALTER TABLE p2p_pages ADD COLUMN campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_p2p_pages_campaign_id ON p2p_pages(campaign_id);
