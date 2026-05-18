-- Migration 0031: repair legacy users tables missing MFA enrollment columns

ALTER TABLE users ADD COLUMN mfa_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN mfa_totp_secret VARCHAR(64);
ALTER TABLE users ADD COLUMN mfa_backup_codes_json JSON;
