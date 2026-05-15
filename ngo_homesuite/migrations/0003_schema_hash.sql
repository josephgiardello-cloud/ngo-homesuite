-- Migration 0003: Add schema hash table for versioned migrations

CREATE TABLE IF NOT EXISTS schema_hash (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    hash TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

-- Optionally, backfill with the current schema hash for the latest version
INSERT INTO schema_hash (version, hash, applied_at_utc)
SELECT version, hash, applied_at_utc FROM schema_version
WHERE version = (SELECT MAX(version) FROM schema_version);
