-- Migration 0004: V2 workflow runtime persistence (definitions, instances, events)

CREATE TABLE IF NOT EXISTS workflow_definitions_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    initial_step TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    transitions_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CONSTRAINT uq_workflow_type_version UNIQUE (workflow_type, version),
    CONSTRAINT ck_workflow_def_type_not_empty CHECK (LENGTH(workflow_type) > 0)
);

CREATE TABLE IF NOT EXISTS workflow_instances_v2 (
    instance_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL,
    current_step TEXT NOT NULL,
    status TEXT NOT NULL,
    history_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CONSTRAINT ck_workflow_instance_org_not_empty CHECK (LENGTH(org_id) > 0)
);

CREATE TABLE IF NOT EXISTS workflow_events_v2 (
    event_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL,
    CONSTRAINT ck_workflow_event_org_not_empty CHECK (LENGTH(org_id) > 0)
);

CREATE INDEX IF NOT EXISTS ix_workflow_instances_v2_org_id ON workflow_instances_v2(org_id);
CREATE INDEX IF NOT EXISTS ix_workflow_instances_v2_workflow_type ON workflow_instances_v2(workflow_type);
CREATE INDEX IF NOT EXISTS ix_workflow_instances_org_workflow ON workflow_instances_v2(org_id, workflow_type);
CREATE INDEX IF NOT EXISTS ix_workflow_events_v2_org_id ON workflow_events_v2(org_id);
CREATE INDEX IF NOT EXISTS ix_workflow_events_v2_event_type ON workflow_events_v2(event_type);
CREATE INDEX IF NOT EXISTS ix_workflow_events_v2_aggregate_id ON workflow_events_v2(aggregate_id);
CREATE INDEX IF NOT EXISTS ix_workflow_events_v2_occurred_at ON workflow_events_v2(occurred_at);
CREATE INDEX IF NOT EXISTS ix_workflow_events_org_occurred ON workflow_events_v2(org_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_workflow_events_org_aggregate ON workflow_events_v2(org_id, aggregate_id);
