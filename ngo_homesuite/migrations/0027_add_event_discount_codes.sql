-- Migration 0027: add event discount code support

CREATE TABLE IF NOT EXISTS event_discount_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    discount_type TEXT NOT NULL,
    discount_value REAL NOT NULL,
    usage_limit INTEGER,
    usage_count INTEGER NOT NULL DEFAULT 0,
    expires_at DATETIME,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id),
    UNIQUE (event_id, code)
);

CREATE INDEX IF NOT EXISTS idx_event_discount_codes_event ON event_discount_codes(event_id);
CREATE INDEX IF NOT EXISTS idx_event_discount_codes_code ON event_discount_codes(code);
CREATE INDEX IF NOT EXISTS idx_event_discount_codes_active ON event_discount_codes(is_active);
CREATE INDEX IF NOT EXISTS idx_event_discount_codes_expires_at ON event_discount_codes(expires_at);
