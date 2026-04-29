ALTER TABLE runs ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);
CREATE INDEX IF NOT EXISTS ix_runs_user_id ON runs(user_id);
