-- Add progress tracking columns to the runs table.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS progress_pct FLOAT NOT NULL DEFAULT 0.0;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS progress_status TEXT;
