-- Migration 0012: Add optimistic locking version columns to donation and fund tables

ALTER TABLE donations ADD COLUMN version_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE funds ADD COLUMN version_id INTEGER NOT NULL DEFAULT 0;