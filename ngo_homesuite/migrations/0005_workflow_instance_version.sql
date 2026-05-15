-- Migration 0005: add optimistic locking version to workflow instances

ALTER TABLE workflow_instances_v2 ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
