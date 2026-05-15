-- Migration 0011: Follow-up reminder tracking fields

ALTER TABLE program_case_followups ADD COLUMN reminder_channel TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE program_case_followups ADD COLUMN reminder_sent_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE program_case_followups ADD COLUMN last_reminder_sent_at TEXT;
ALTER TABLE program_case_followups ADD COLUMN last_reminder_error TEXT;
