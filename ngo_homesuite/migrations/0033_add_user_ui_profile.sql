-- Migration 0033: add per-user UI profile JSON for server-persisted personalization

ALTER TABLE users ADD COLUMN ui_profile_json JSON;
