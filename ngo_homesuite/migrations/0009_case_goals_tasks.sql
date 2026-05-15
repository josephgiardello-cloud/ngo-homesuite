-- Migration 0009: Case-plan goals, tasks, and milestone tracking

CREATE TABLE IF NOT EXISTS program_case_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL REFERENCES program_cases(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    metric_name TEXT,
    target_value REAL,
    current_value REAL,
    unit TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    target_date DATE,
    achieved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_program_case_goals_org ON program_case_goals(organization_id);
CREATE INDEX IF NOT EXISTS ix_program_case_goals_case ON program_case_goals(case_id);
CREATE INDEX IF NOT EXISTS ix_program_case_goals_status ON program_case_goals(status);
CREATE INDEX IF NOT EXISTS ix_program_case_goals_target_date ON program_case_goals(target_date);

CREATE TABLE IF NOT EXISTS program_case_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL REFERENCES program_cases(id) ON DELETE CASCADE,
    goal_id INTEGER REFERENCES program_case_goals(id) ON DELETE SET NULL,
    assigned_to_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT NOT NULL DEFAULT 'medium',
    due_date DATE,
    is_milestone INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_program_case_tasks_org ON program_case_tasks(organization_id);
CREATE INDEX IF NOT EXISTS ix_program_case_tasks_case ON program_case_tasks(case_id);
CREATE INDEX IF NOT EXISTS ix_program_case_tasks_goal ON program_case_tasks(goal_id);
CREATE INDEX IF NOT EXISTS ix_program_case_tasks_status ON program_case_tasks(status);
CREATE INDEX IF NOT EXISTS ix_program_case_tasks_due_date ON program_case_tasks(due_date);
CREATE INDEX IF NOT EXISTS ix_program_case_tasks_milestone ON program_case_tasks(is_milestone);
