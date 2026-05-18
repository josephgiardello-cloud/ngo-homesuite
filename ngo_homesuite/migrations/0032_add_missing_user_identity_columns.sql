-- Migration 0032: repair legacy users tables missing identity columns

ALTER TABLE users ADD COLUMN oauth_provider VARCHAR(32);
ALTER TABLE users ADD COLUMN oauth_provider_id VARCHAR(256);
ALTER TABLE users ADD COLUMN webauthn_credentials_json JSON;

CREATE INDEX IF NOT EXISTS idx_users_oauth_provider ON users(oauth_provider);
CREATE INDEX IF NOT EXISTS idx_users_oauth_provider_id ON users(oauth_provider_id);