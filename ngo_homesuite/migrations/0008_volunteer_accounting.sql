-- Migration 0008: Volunteer scheduling, training, and accounting sync tables

-- Volunteer shifts: scheduled/completed time contributions
CREATE TABLE IF NOT EXISTS volunteer_shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    volunteer_id INTEGER NOT NULL REFERENCES volunteers(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    shift_date DATE NOT NULL,
    start_time TEXT,         -- HH:MM 24h
    end_time TEXT,           -- HH:MM 24h
    hours REAL,
    location TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_volunteer_shifts_org ON volunteer_shifts(organization_id);
CREATE INDEX IF NOT EXISTS ix_volunteer_shifts_vol ON volunteer_shifts(volunteer_id);
CREATE INDEX IF NOT EXISTS ix_volunteer_shifts_date ON volunteer_shifts(shift_date);

-- Training course definitions
CREATE TABLE IF NOT EXISTS training_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL DEFAULT 'orientation',
    duration_hours REAL,
    is_required INTEGER NOT NULL DEFAULT 0,
    expires_after_days INTEGER,
    created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_training_courses_org ON training_courses(organization_id);

-- Volunteer training assignment and completion records
CREATE TABLE IF NOT EXISTS volunteer_trainings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    volunteer_id INTEGER NOT NULL REFERENCES volunteers(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES training_courses(id) ON DELETE CASCADE,
    assigned_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    score REAL,
    expires_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_volunteer_trainings_org ON volunteer_trainings(organization_id);
CREATE INDEX IF NOT EXISTS ix_volunteer_trainings_vol ON volunteer_trainings(volunteer_id);
CREATE INDEX IF NOT EXISTS ix_volunteer_trainings_course ON volunteer_trainings(course_id);

-- Accounting sync audit log (QuickBooks, Xero)
CREATE TABLE IF NOT EXISTS accounting_sync_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    sync_type TEXT NOT NULL,
    internal_id INTEGER,
    external_id TEXT,
    external_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_accounting_sync_org ON accounting_sync_logs(organization_id);
CREATE INDEX IF NOT EXISTS ix_accounting_sync_provider ON accounting_sync_logs(provider);
CREATE INDEX IF NOT EXISTS ix_accounting_sync_status ON accounting_sync_logs(status);
