-- 0036_add_donor_journey_automation_events.sql
-- Durable audit + idempotency ledger for donor automation runs.

BEGIN;

CREATE TABLE IF NOT EXISTS donor_journey_automation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    donor_id INTEGER,
    recurring_plan_id INTEGER,
    trigger_name TEXT NOT NULL,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'executed', -- executed, skipped, failed
    idempotency_key TEXT NOT NULL,
    cooldown_until DATETIME,
    reason TEXT,
    payload_json TEXT,
    actor_user_id INTEGER,
    related_task_id INTEGER,
    related_enrollment_id INTEGER,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (donor_id) REFERENCES donors(id),
    FOREIGN KEY (recurring_plan_id) REFERENCES recurring_donation_plans(id),
    FOREIGN KEY (actor_user_id) REFERENCES users(id),
    FOREIGN KEY (related_task_id) REFERENCES tasks(id),
    FOREIGN KEY (related_enrollment_id) REFERENCES stewardship_enrollments(id),
    UNIQUE (organization_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_djae_org_trigger_created
    ON donor_journey_automation_events (organization_id, trigger_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_djae_org_donor_created
    ON donor_journey_automation_events (organization_id, donor_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_djae_org_recurring_created
    ON donor_journey_automation_events (organization_id, recurring_plan_id, created_at DESC);

COMMIT;
