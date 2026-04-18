-- Migration 001: OAuth provider columns on users
-- Run once: psql -d convey_agent -f this_file.sql
-- Idempotent via DO blocks.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='users' AND column_name='oauth_provider'
    ) THEN
        ALTER TABLE users ADD COLUMN oauth_provider TEXT;
        ALTER TABLE users ADD COLUMN oauth_subject  TEXT;
        -- oauth users have no password, so drop the NOT NULL assumption
        -- (password_hash is already nullable — this is fine)
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_oauth
            ON users(oauth_provider, oauth_subject)
            WHERE oauth_provider IS NOT NULL;
    END IF;
END$$;
