-- 0020_donor_campaign_photos.sql
-- Add photo path storage for donor and campaign profile images

ALTER TABLE donors ADD COLUMN photo_path TEXT;
ALTER TABLE campaigns ADD COLUMN photo_path TEXT;
