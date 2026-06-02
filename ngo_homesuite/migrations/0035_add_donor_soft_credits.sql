-- Migration 0035: add explicit donor soft-credit attribution

CREATE TABLE IF NOT EXISTS donor_soft_credits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    donation_id INTEGER NOT NULL REFERENCES donations(id) ON DELETE CASCADE,
    donor_id INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'influencer',
    credited_amount REAL NOT NULL,
    credit_weight REAL NOT NULL DEFAULT 1.0,
    rationale TEXT,
    attributed_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (organization_id, donation_id, donor_id, role)
);

CREATE INDEX IF NOT EXISTS ix_donor_soft_credits_org_id ON donor_soft_credits(organization_id);
CREATE INDEX IF NOT EXISTS ix_donor_soft_credits_donation_id ON donor_soft_credits(donation_id);
CREATE INDEX IF NOT EXISTS ix_donor_soft_credits_donor_id ON donor_soft_credits(donor_id);
CREATE INDEX IF NOT EXISTS ix_donor_soft_credits_created_at ON donor_soft_credits(created_at);

-- Backfill legacy soft-credit strings to relational records when a donor name match exists.
INSERT OR IGNORE INTO donor_soft_credits (
    organization_id,
    donation_id,
    donor_id,
    role,
    credited_amount,
    credit_weight,
    rationale,
    attributed_by_user_id,
    created_at
)
SELECT
    don.organization_id,
    don.id,
    d.id,
    'influencer',
    COALESCE(don.amount, 0.0),
    1.0,
    'Backfilled from donations.soft_credit_name',
    NULL,
    COALESCE(don.created_at, CURRENT_TIMESTAMP)
FROM donations don
JOIN donors d
    ON d.organization_id = don.organization_id
   AND lower(trim(d.name)) = lower(trim(don.soft_credit_name))
WHERE don.soft_credit_name IS NOT NULL
  AND trim(don.soft_credit_name) <> '';
