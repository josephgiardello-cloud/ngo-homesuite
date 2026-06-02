-- 0037_add_form_submission_events.sql
-- Integrated form ecosystem submission ledger with idempotency and CRM linkage.

BEGIN;

CREATE TABLE IF NOT EXISTS form_submission_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    form_name TEXT,
    form_type TEXT NOT NULL,
    external_submission_id TEXT,
    idempotency_key TEXT NOT NULL,
    submitter_name TEXT,
    submitter_email TEXT,
    submitter_phone TEXT,
    donor_id INTEGER,
    donation_id INTEGER,
    task_id INTEGER,
    amount REAL,
    currency TEXT,
    message TEXT,
    metadata_json TEXT,
    raw_payload_json TEXT,
    submitted_at DATETIME,
    processed_at DATETIME,
    status TEXT NOT NULL DEFAULT 'processed',
    error_message TEXT,
    actor_user_id INTEGER,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (donor_id) REFERENCES donors(id),
    FOREIGN KEY (donation_id) REFERENCES donations(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (actor_user_id) REFERENCES users(id),
    UNIQUE (organization_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_form_submission_events_org_source_submitted
    ON form_submission_events (organization_id, source, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_form_submission_events_org_type_submitted
    ON form_submission_events (organization_id, form_type, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_form_submission_events_org_donor_created
    ON form_submission_events (organization_id, donor_id, created_at DESC);

COMMIT;
