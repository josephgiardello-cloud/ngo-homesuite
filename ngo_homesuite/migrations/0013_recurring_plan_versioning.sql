-- Add optimistic locking column for recurring donation plans.
ALTER TABLE recurring_donation_plans ADD COLUMN version_id INTEGER NOT NULL DEFAULT 0;
