-- Migration 002: pgvector extension + memories table (20-day TTL vector store)
-- Run once: psql -d convey_agent -f 002_vector_memory.sql
-- Fully idempotent.

-- 1. Enable pgvector extension (requires PostgreSQL with pgvector installed)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create the memories table
CREATE TABLE IF NOT EXISTS memories (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT        NOT NULL,
    session_id  TEXT        NOT NULL,
    content     TEXT        NOT NULL,
    embedding   vector(1536) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL
);

-- 3. Indexes for fast lookup
CREATE INDEX IF NOT EXISTS ix_memories_user_id
    ON memories(user_id);

CREATE INDEX IF NOT EXISTS ix_memories_expires_at
    ON memories(expires_at);

-- 4. IVFFlat index for approximate nearest-neighbour cosine search.
--    lists=100 is a reasonable default for up to ~1M rows.
--    Rebuild with higher lists if the table grows significantly.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'memories'
          AND indexname  = 'ix_memories_embedding_cosine'
    ) THEN
        CREATE INDEX ix_memories_embedding_cosine
            ON memories USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100);
    END IF;
END$$;

-- 5. Verify the column was created correctly (informational only)
-- SELECT column_name, data_type, udt_name
-- FROM   information_schema.columns
-- WHERE  table_name = 'memories';
