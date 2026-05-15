-- Migration 0010: Case documents and follow-up workflow (reminders/escalations)

CREATE TABLE IF NOT EXISTS program_case_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL REFERENCES program_cases(id) ON DELETE CASCADE,
    uploaded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    category TEXT NOT NULL DEFAULT 'attachment',
    title TEXT NOT NULL,
    file_name TEXT,
    mime_type TEXT,
    storage_key TEXT,
    external_url TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_program_case_documents_org ON program_case_documents(organization_id);
CREATE INDEX IF NOT EXISTS ix_program_case_documents_case ON program_case_documents(case_id);
CREATE INDEX IF NOT EXISTS ix_program_case_documents_category ON program_case_documents(category);

CREATE TABLE IF NOT EXISTS program_case_followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL REFERENCES program_cases(id) ON DELETE CASCADE,
    beneficiary_id INTEGER REFERENCES beneficiaries(id) ON DELETE SET NULL,
    assigned_to_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled',
    follow_up_type TEXT NOT NULL DEFAULT 'general',
    due_at TEXT NOT NULL,
    reminder_at TEXT,
    escalation_level INTEGER NOT NULL DEFAULT 0,
    escalation_reason TEXT,
    escalated_at TEXT,
    completed_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_program_case_followups_org ON program_case_followups(organization_id);
CREATE INDEX IF NOT EXISTS ix_program_case_followups_case ON program_case_followups(case_id);
CREATE INDEX IF NOT EXISTS ix_program_case_followups_beneficiary ON program_case_followups(beneficiary_id);
CREATE INDEX IF NOT EXISTS ix_program_case_followups_status ON program_case_followups(status);
CREATE INDEX IF NOT EXISTS ix_program_case_followups_due_at ON program_case_followups(due_at);
CREATE INDEX IF NOT EXISTS ix_program_case_followups_reminder_at ON program_case_followups(reminder_at);
