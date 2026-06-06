-- Migration 0006: Gap Analysis Feature Modules
-- Adds tables for: Grants, Memberships, Stewardship Journeys,
--                  Tasks, Program Cases, Engagement Scores,
--                  Smart Groups, and P2P Fundraising

-- ============================================================
-- GRANTS
-- ============================================================
CREATE TABLE IF NOT EXISTS grants (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id          INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    funder_name         TEXT    NOT NULL,
    funder_type         TEXT    NOT NULL DEFAULT 'foundation',
    funder_contact      TEXT,
    funder_email        TEXT,
    title               TEXT    NOT NULL,
    description         TEXT,
    amount_requested    REAL,
    amount_awarded      REAL,
    currency            TEXT    NOT NULL DEFAULT 'USD',
    application_deadline DATE,
    submission_date      DATE,
    award_date           DATE,
    start_date           DATE,
    end_date             DATE,
    report_due_date      DATE,
    status              TEXT    NOT NULL DEFAULT 'prospect'
                        CHECK (status IN ('prospect','in_progress','submitted','awarded','declined','reporting','closed')),
    requirements        TEXT,
    notes               TEXT,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_grants_organization_id ON grants(organization_id);
CREATE INDEX IF NOT EXISTS ix_grants_status          ON grants(status);
CREATE INDEX IF NOT EXISTS ix_grants_application_deadline ON grants(application_deadline);

CREATE TABLE IF NOT EXISTS grant_disbursements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_id        INTEGER NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    amount          REAL    NOT NULL,
    currency        TEXT    NOT NULL DEFAULT 'USD',
    received_date   DATE    NOT NULL,
    reference       TEXT,
    notes           TEXT,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_grant_disbursements_grant_id ON grant_disbursements(grant_id);
CREATE INDEX IF NOT EXISTS ix_grant_disbursements_org_id ON grant_disbursements(organization_id);

-- ============================================================
-- MEMBERSHIPS
-- ============================================================
CREATE TABLE IF NOT EXISTS membership_tiers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,
    price           REAL    NOT NULL DEFAULT 0,
    currency        TEXT    NOT NULL DEFAULT 'USD',
    interval        TEXT    NOT NULL DEFAULT 'annual'
                    CHECK (interval IN ('monthly','quarterly','annual')),
    benefits        TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (organization_id, name)
);

CREATE TABLE IF NOT EXISTS membership_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    donor_id            INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    tier_id             INTEGER NOT NULL REFERENCES membership_tiers(id) ON DELETE RESTRICT,
    start_date          DATE    NOT NULL,
    end_date            DATE,
    next_renewal_date   DATE,
    status              TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','lapsed','cancelled')),
    payment_reference   TEXT,
    notes               TEXT,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_membership_records_org_donor  ON membership_records(organization_id, donor_id);
CREATE INDEX IF NOT EXISTS ix_membership_records_status     ON membership_records(status);
CREATE INDEX IF NOT EXISTS ix_membership_records_end_date   ON membership_records(end_date);
CREATE INDEX IF NOT EXISTS ix_membership_records_next_renewal_date ON membership_records(next_renewal_date);

-- ============================================================
-- STEWARDSHIP JOURNEYS
-- ============================================================
CREATE TABLE IF NOT EXISTS stewardship_journeys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,
    trigger         TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stewardship_steps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    journey_id    INTEGER NOT NULL REFERENCES stewardship_journeys(id) ON DELETE CASCADE,
    step_order    INTEGER NOT NULL DEFAULT 0,
    step_type     TEXT    NOT NULL CHECK (step_type IN ('email','sms','wait')),
    delay_days    INTEGER NOT NULL DEFAULT 0,
    template_name TEXT,
    subject       TEXT,
    body          TEXT,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_stewardship_steps_journey_id ON stewardship_steps(journey_id, step_order);

CREATE TABLE IF NOT EXISTS stewardship_enrollments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    journey_id      INTEGER NOT NULL REFERENCES stewardship_journeys(id) ON DELETE CASCADE,
    donor_id        INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    status          TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','completed','cancelled')),
    current_step    INTEGER NOT NULL DEFAULT 0,
    enrolled_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_step_due   DATETIME,
    completed_at    DATETIME
);

CREATE INDEX IF NOT EXISTS ix_stewardship_enrollments_org      ON stewardship_enrollments(organization_id);
CREATE INDEX IF NOT EXISTS ix_stewardship_enrollments_due      ON stewardship_enrollments(next_step_due);
CREATE INDEX IF NOT EXISTS ix_stewardship_enrollments_status   ON stewardship_enrollments(status);

-- ============================================================
-- TASKS (Moves Management)
-- ============================================================
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    title           TEXT    NOT NULL,
    description     TEXT,
    task_type       TEXT    NOT NULL DEFAULT 'general',
    priority        TEXT    NOT NULL DEFAULT 'medium'
                    CHECK (priority IN ('low','medium','high','urgent')),
    status          TEXT    NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','in_progress','done','cancelled')),
    due_date        DATETIME,
    completed_at    DATETIME,
    assigned_to_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    donor_id        INTEGER REFERENCES donors(id) ON DELETE SET NULL,
    grant_id        INTEGER REFERENCES grants(id) ON DELETE SET NULL,
    donation_id     INTEGER REFERENCES donations(id) ON DELETE SET NULL,
    project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    notes           TEXT,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_tasks_organization_id ON tasks(organization_id);
CREATE INDEX IF NOT EXISTS ix_tasks_assigned_to_id  ON tasks(assigned_to_id);
CREATE INDEX IF NOT EXISTS ix_tasks_donor_id        ON tasks(donor_id);
CREATE INDEX IF NOT EXISTS ix_tasks_grant_id        ON tasks(grant_id);
CREATE INDEX IF NOT EXISTS ix_tasks_donation_id     ON tasks(donation_id);
CREATE INDEX IF NOT EXISTS ix_tasks_project_id      ON tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_tasks_status          ON tasks(status);
CREATE INDEX IF NOT EXISTS ix_tasks_due_date        ON tasks(due_date);

-- ============================================================
-- PROGRAM CASES (Impact Tracking)
-- ============================================================
CREATE TABLE IF NOT EXISTS program_cases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    grant_id        INTEGER REFERENCES grants(id) ON DELETE SET NULL,
    donor_id        INTEGER REFERENCES donors(id) ON DELETE SET NULL,
    beneficiary_id  INTEGER REFERENCES beneficiaries(id) ON DELETE SET NULL,
    title           TEXT    NOT NULL,
    case_type       TEXT    NOT NULL DEFAULT 'service',
    status          TEXT    NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','in_progress','closed','on_hold')),
    priority        TEXT    NOT NULL DEFAULT 'medium',
    description     TEXT,
    outcome         TEXT,
    outcome_metric  TEXT,
    outcome_value   REAL,
    opened_date     DATE,
    closed_date     DATE,
    next_review_date DATE,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_program_cases_organization_id ON program_cases(organization_id);
CREATE INDEX IF NOT EXISTS ix_program_cases_status          ON program_cases(status);
CREATE INDEX IF NOT EXISTS ix_program_cases_case_type       ON program_cases(case_type);
CREATE INDEX IF NOT EXISTS ix_program_cases_next_review_date ON program_cases(next_review_date);

CREATE TABLE IF NOT EXISTS case_activities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         INTEGER NOT NULL REFERENCES program_cases(id) ON DELETE CASCADE,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    activity_type   TEXT    NOT NULL,
    content         TEXT,
    previous_status TEXT,
    new_status      TEXT,
    actor_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_case_activities_case_id ON case_activities(case_id);
CREATE INDEX IF NOT EXISTS ix_case_activities_org_id ON case_activities(organization_id);

-- ============================================================
-- DONOR ENGAGEMENT SCORES
-- ============================================================
CREATE TABLE IF NOT EXISTS donor_engagement_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    donor_id            INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    score               REAL    NOT NULL DEFAULT 0,
    recency_score       REAL    NOT NULL DEFAULT 0,
    frequency_score     REAL    NOT NULL DEFAULT 0,
    monetary_score      REAL    NOT NULL DEFAULT 0,
    engagement_score    REAL    NOT NULL DEFAULT 0,
    segment             TEXT,
    cultivation_priority TEXT    NOT NULL DEFAULT 'medium',
    explanation         TEXT,
    computed_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (organization_id, donor_id)
);

CREATE INDEX IF NOT EXISTS ix_donor_engagement_scores_org    ON donor_engagement_scores(organization_id);
CREATE INDEX IF NOT EXISTS ix_donor_engagement_scores_score  ON donor_engagement_scores(score DESC);
CREATE INDEX IF NOT EXISTS ix_donor_engagement_scores_segment ON donor_engagement_scores(segment);

-- ============================================================
-- SMART GROUPS (Dynamic Audiences)
-- ============================================================
CREATE TABLE IF NOT EXISTS smart_groups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name                TEXT    NOT NULL,
    description         TEXT,
    rules_json          TEXT    NOT NULL DEFAULT '[]',
    is_active           INTEGER NOT NULL DEFAULT 1,
    last_evaluated_at   DATETIME,
    last_count          INTEGER NOT NULL DEFAULT 0,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (organization_id, name)
);

CREATE INDEX IF NOT EXISTS ix_smart_groups_organization_id ON smart_groups(organization_id);

-- ============================================================
-- P2P FUNDRAISING
-- ============================================================
CREATE TABLE IF NOT EXISTS p2p_pages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    donor_id        INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    campaign_slug   TEXT,
    title           TEXT    NOT NULL,
    story           TEXT,
    goal_amount     REAL    NOT NULL DEFAULT 0,
    currency        TEXT    NOT NULL DEFAULT 'USD',
    status          TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('draft','active','closed')),
    public_slug     TEXT    NOT NULL UNIQUE,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_p2p_pages_organization_id ON p2p_pages(organization_id);
CREATE INDEX IF NOT EXISTS ix_p2p_pages_donor_id        ON p2p_pages(donor_id);
CREATE INDEX IF NOT EXISTS ix_p2p_pages_campaign_slug   ON p2p_pages(campaign_slug);
CREATE INDEX IF NOT EXISTS ix_p2p_pages_public_slug     ON p2p_pages(public_slug);
CREATE INDEX IF NOT EXISTS ix_p2p_pages_status          ON p2p_pages(status);

CREATE TABLE IF NOT EXISTS p2p_page_donations (
    page_id     INTEGER NOT NULL REFERENCES p2p_pages(id) ON DELETE CASCADE,
    donation_id INTEGER NOT NULL REFERENCES donations(id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, donation_id)
);

CREATE INDEX IF NOT EXISTS ix_p2p_page_donations_page_id     ON p2p_page_donations(page_id);
CREATE INDEX IF NOT EXISTS ix_p2p_page_donations_donation_id ON p2p_page_donations(donation_id);
