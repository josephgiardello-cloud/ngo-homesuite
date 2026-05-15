-- 0007_beneficiary_case_management_expansion.sql
-- Expand beneficiary/case management with intake/progress fields
-- and structured service/outcome tracking tables.

ALTER TABLE program_cases ADD COLUMN target_outcome_value REAL;
ALTER TABLE program_cases ADD COLUMN progress_percent REAL NOT NULL DEFAULT 0;
ALTER TABLE program_cases ADD COLUMN intake_stage TEXT NOT NULL DEFAULT 'intake';
ALTER TABLE program_cases ADD COLUMN risk_level TEXT;
ALTER TABLE program_cases ADD COLUMN intake_summary TEXT;

CREATE TABLE IF NOT EXISTS beneficiary_service_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id  INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    case_id          INTEGER NOT NULL REFERENCES program_cases(id) ON DELETE CASCADE,
    beneficiary_id   INTEGER REFERENCES beneficiaries(id) ON DELETE SET NULL,
    staff_user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    service_type     TEXT    NOT NULL,
    service_date     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_minutes INTEGER,
    service_units    REAL,
    outcome_note     TEXT,
    metadata         TEXT,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_beneficiary_service_logs_org_id ON beneficiary_service_logs(organization_id);
CREATE INDEX IF NOT EXISTS ix_beneficiary_service_logs_case_id ON beneficiary_service_logs(case_id);
CREATE INDEX IF NOT EXISTS ix_beneficiary_service_logs_beneficiary_id ON beneficiary_service_logs(beneficiary_id);
CREATE INDEX IF NOT EXISTS ix_beneficiary_service_logs_service_date ON beneficiary_service_logs(service_date);

CREATE TABLE IF NOT EXISTS case_outcome_metrics (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    case_id        INTEGER NOT NULL REFERENCES program_cases(id) ON DELETE CASCADE,
    metric_name    TEXT    NOT NULL,
    unit           TEXT,
    baseline_value REAL,
    target_value   REAL,
    current_value  REAL    NOT NULL,
    recorded_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note           TEXT
);

CREATE INDEX IF NOT EXISTS ix_case_outcome_metrics_org_id ON case_outcome_metrics(organization_id);
CREATE INDEX IF NOT EXISTS ix_case_outcome_metrics_case_id ON case_outcome_metrics(case_id);
CREATE INDEX IF NOT EXISTS ix_case_outcome_metrics_recorded_at ON case_outcome_metrics(recorded_at);
