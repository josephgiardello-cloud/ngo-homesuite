-- Migration 0024: add TOTP policy flag to users and backfill admins

ALTER TABLE users ADD COLUMN totp_required_flag INTEGER NOT NULL DEFAULT 0;

UPDATE users
SET totp_required_flag = 1
WHERE lower(role) = 'admin';
