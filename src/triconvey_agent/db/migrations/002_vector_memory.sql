-- Migration 002: pgvector extension + memories table (20-day TTL vector store)
-- Fully idempotent and self-guarding: exits cleanly when pgvector is not installed.

DO $$
BEGIN
    -- Guard: only proceed if pgvector is available on this PostgreSQL installation.
    IF NOT EXISTS (
        SELECT 1 FROM pg_available_extensions WHERE name = 'vector'
    ) THEN
        RAISE NOTICE 'pgvector extension not available — migration 002 skipped (memory features disabled).';
        RETURN;
    END IF;

    -- 1. Enable pgvector extension.
    EXECUTE 'CREATE EXTENSION IF NOT EXISTS vector';

    -- 2. Create the memories table.
    EXECUTE $ddl$
        CREATE TABLE IF NOT EXISTS memories (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     TEXT        NOT NULL,
            session_id  TEXT        NOT NULL,
            content     TEXT        NOT NULL,
            embedding   vector(1536) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NOT NULL
        )
    $ddl$;

    -- 3. Basic indexes.
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'memories' AND indexname = 'ix_memories_user_id') THEN
        EXECUTE 'CREATE INDEX ix_memories_user_id ON memories(user_id)';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'memories' AND indexname = 'ix_memories_expires_at') THEN
        EXECUTE 'CREATE INDEX ix_memories_expires_at ON memories(expires_at)';
    END IF;

    -- 4. IVFFlat index for approximate nearest-neighbour cosine search.
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'memories' AND indexname = 'ix_memories_embedding_cosine') THEN
        EXECUTE 'CREATE INDEX ix_memories_embedding_cosine ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)';
    END IF;

END$$;
