-- 0039_add_project_milestones_and_task_dependencies.sql
-- Adds project milestone tracking and explicit task dependency edges.

CREATE TABLE IF NOT EXISTS project_milestones (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    owner_user_id INTEGER,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (owner_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_project_milestones_org_project ON project_milestones(organization_id, project_id);
CREATE INDEX IF NOT EXISTS idx_project_milestones_status ON project_milestones(status);

CREATE TABLE IF NOT EXISTS task_dependencies (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    depends_on_task_id INTEGER NOT NULL,
    dependency_type TEXT NOT NULL DEFAULT 'blocks',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (depends_on_task_id) REFERENCES tasks(id),
    UNIQUE (organization_id, task_id, depends_on_task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_dependencies_org_task ON task_dependencies(organization_id, task_id);
CREATE INDEX IF NOT EXISTS idx_task_dependencies_org_depends ON task_dependencies(organization_id, depends_on_task_id);
