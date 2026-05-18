-- Migration 0030: add persisted version column to workflow events
ALTER TABLE workflow_events_v2 ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
