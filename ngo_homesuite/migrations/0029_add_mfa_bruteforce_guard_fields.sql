-- Migration 0029: add MFA challenge brute-force guard fields to users

ALTER TABLE users ADD COLUMN mfa_failed_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN mfa_attempt_window_started_at DATETIME;
ALTER TABLE users ADD COLUMN mfa_locked_until DATETIME;

CREATE INDEX IF NOT EXISTS idx_users_mfa_locked_until ON users(mfa_locked_until);
