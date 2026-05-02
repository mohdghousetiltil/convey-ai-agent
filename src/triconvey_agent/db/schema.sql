-- ============================================================================
-- Convey Agent — Postgres schema (reference, matches ORM in models.py).
--
-- Used for:
--   1. First-time installs (fresh DB bootstrap on a client's PC).
--   2. Comparing against live DB when diagnosing drift.
--
-- For ongoing schema changes, use Alembic (see `alembic/` at repo root).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector (Feature: vector memory)

-- ---------- CLIENTS & USERS ------------------------------------------------

CREATE TABLE IF NOT EXISTS clients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    license_key     TEXT UNIQUE,
    version         TEXT,
    policy_json     JSONB NOT NULL DEFAULT '{}',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_synced_at  TIMESTAMPTZ,
    sync_enabled    BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    name            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'reviewer',
    password_hash   TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ,
    oauth_provider  TEXT,
    oauth_subject   TEXT,
    CONSTRAINT uq_users_client_email UNIQUE(client_id, email)
);
CREATE INDEX IF NOT EXISTS ix_users_client_id ON users(client_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_oauth
    ON users(oauth_provider, oauth_subject)
    WHERE oauth_provider IS NOT NULL;

CREATE TABLE IF NOT EXISTS sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT UNIQUE NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address      INET,
    user_agent      TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_token_hash ON sessions(token_hash);

-- ---------- FORM TEMPLATES (declared early — referenced by runs) ----------

CREATE TABLE IF NOT EXISTS form_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    version         TEXT UNIQUE NOT NULL,
    tabs_json       JSONB NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------- MATTERS / RUNS / DOCUMENTS -----------------------------------

CREATE TABLE IF NOT EXISTS matters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    matter_ref      TEXT,
    property_address TEXT,
    volume_folio    TEXT,
    council_area    TEXT,
    water_authority TEXT,
    property_type   TEXT,
    vendor_count    INT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_run_id     UUID,
    last_run_at     TIMESTAMPTZ,
    CONSTRAINT uq_matters_client_ref UNIQUE(client_id, matter_ref)
);
CREATE INDEX IF NOT EXISTS ix_matters_client_status ON matters(client_id, status);
CREATE INDEX IF NOT EXISTS ix_matters_volume_folio ON matters(volume_folio);

CREATE TABLE IF NOT EXISTS runs (
    id              UUID PRIMARY KEY,
    matter_id       UUID REFERENCES matters(id),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES users(id),
    status          TEXT NOT NULL DEFAULT 'pending',
    use_ai_review   BOOLEAN NOT NULL DEFAULT FALSE,
    model           TEXT NOT NULL DEFAULT 'gpt-4.1-mini',
    document_count  INT,
    total_facts     INT,
    summary_text    TEXT,
    metrics_json    JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    error_message   TEXT,
    form_template_id UUID REFERENCES form_templates(id),
    is_synced       BOOLEAN NOT NULL DEFAULT FALSE,
    synced_at       TIMESTAMPTZ,
    local_only      BOOLEAN NOT NULL DEFAULT FALSE,
    progress_pct    FLOAT NOT NULL DEFAULT 0.0,
    progress_status TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_client_status ON runs(client_id, status);
CREATE INDEX IF NOT EXISTS ix_runs_user_id ON runs(user_id);
CREATE INDEX IF NOT EXISTS ix_runs_matter_id ON runs(matter_id);
CREATE INDEX IF NOT EXISTS ix_runs_is_synced ON runs(is_synced);

CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    file_type       TEXT NOT NULL,
    document_type   TEXT NOT NULL,
    file_size_bytes INT,
    page_count      INT,
    char_count      INT,
    classification_confidence FLOAT,
    classification_reasons  TEXT[],
    metadata_json   JSONB NOT NULL DEFAULT '{}',
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    marked_for_deletion BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_documents_run_id ON documents(run_id);

-- ---------- AUTHORITY RULES (declared before fact_conflicts refs it) -----

CREATE TABLE IF NOT EXISTS authority_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path_pattern    TEXT NOT NULL,
    authoritative_extractors TEXT[] NOT NULL,
    on_unresolved   TEXT NOT NULL,
    note            TEXT,
    rule_version    TEXT NOT NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_authority_rules_run_id ON authority_rules(run_id);

-- ---------- FACTS / SOURCES / CONFLICTS ----------------------------------

CREATE TABLE IF NOT EXISTS facts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    value_json      JSONB,
    value_type      TEXT,
    confidence      FLOAT NOT NULL,
    extractor       TEXT NOT NULL,
    extracted_at    TIMESTAMPTZ NOT NULL,
    notes           TEXT,
    is_winning      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    needs_sync      BOOLEAN NOT NULL DEFAULT TRUE,
    last_synced_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_facts_run_path ON facts(run_id, path);
CREATE INDEX IF NOT EXISTS ix_facts_needs_sync ON facts(needs_sync);

CREATE TABLE IF NOT EXISTS fact_sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_id         UUID NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    document_id     UUID REFERENCES documents(id),
    file            TEXT NOT NULL,
    page            INT,
    quote           TEXT,
    quote_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    extractor_note  TEXT
);
CREATE INDEX IF NOT EXISTS ix_fact_sources_fact_id ON fact_sources(fact_id);

CREATE TABLE IF NOT EXISTS fact_conflicts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    resolution      TEXT NOT NULL,
    winning_fact_id UUID REFERENCES facts(id),
    reason          TEXT,
    authority_rule_id UUID REFERENCES authority_rules(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_fact_conflicts_run_path ON fact_conflicts(run_id, path);

-- ---------- QUESTIONS / ANSWERS / EVIDENCE / AI REVIEWS ------------------

CREATE TABLE IF NOT EXISTS questions (
    id              TEXT PRIMARY KEY,
    tab             TEXT NOT NULL,
    label           TEXT NOT NULL,
    expected_type   TEXT,
    answer_strategy TEXT,
    fact_paths      TEXT[] NOT NULL,
    options         TEXT[],
    description     TEXT,
    value_transform TEXT NOT NULL DEFAULT 'direct',
    version         INT NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_questions_is_active ON questions(is_active);

CREATE TABLE IF NOT EXISTS answers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    question_id     TEXT NOT NULL REFERENCES questions(id),
    tab             TEXT NOT NULL,
    label           TEXT NOT NULL,
    expected_type   TEXT,
    answer_strategy TEXT,
    options         TEXT[],
    description     TEXT,
    value_json      JSONB,
    confidence      FLOAT NOT NULL DEFAULT 0.0,
    facts_used      TEXT[],
    human_edited    BOOLEAN NOT NULL DEFAULT FALSE,
    human_value_json JSONB,
    human_edited_at TIMESTAMPTZ,
    human_edited_by UUID REFERENCES users(id),
    needs_review    BOOLEAN NOT NULL DEFAULT FALSE,
    review_reasons  TEXT[],
    presentation_hints JSONB NOT NULL DEFAULT '{}',
    needs_sync      BOOLEAN NOT NULL DEFAULT FALSE,
    last_synced_at  TIMESTAMPTZ,
    is_synced       BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_answers_run_question UNIQUE(run_id, question_id)
);
CREATE INDEX IF NOT EXISTS ix_answers_run_id ON answers(run_id);
CREATE INDEX IF NOT EXISTS ix_answers_needs_sync ON answers(needs_sync);
CREATE INDEX IF NOT EXISTS ix_answers_needs_review ON answers(needs_review);

CREATE TABLE IF NOT EXISTS answer_evidence (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id       UUID NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    fact_id         UUID REFERENCES facts(id),
    file            TEXT,
    page            INT,
    quote           TEXT,
    quote_verified  BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_answer_evidence_answer_id ON answer_evidence(answer_id);

CREATE TABLE IF NOT EXISTS ai_reviews (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    answer_id       UUID NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    confidence_delta FLOAT,
    suggestion_text TEXT,
    status          TEXT NOT NULL,
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_by     UUID REFERENCES users(id),
    is_synced       BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_ai_reviews_run_id ON ai_reviews(run_id);

-- ---------- AUTOFILL JOBS / ACTION RESULTS -------------------------------

CREATE TABLE IF NOT EXISTS autofill_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'queued',
    dry_run         BOOLEAN NOT NULL DEFAULT FALSE,
    skip_review_gate BOOLEAN NOT NULL DEFAULT FALSE,
    triconvey_exe   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error           TEXT,
    total_filled    INT,
    total_verified  INT,
    total_failed    INT,
    total_skipped   INT,
    total_pending_review INT,
    is_synced       BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_autofill_jobs_run_id ON autofill_jobs(run_id);

CREATE TABLE IF NOT EXISTS action_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES autofill_jobs(id) ON DELETE CASCADE,
    answer_id       UUID REFERENCES answers(id),
    question_id     TEXT NOT NULL,
    field_id        TEXT NOT NULL,
    action          TEXT NOT NULL,
    payload_json    JSONB,
    status          TEXT NOT NULL,
    actual_value    JSONB,
    error           TEXT,
    attempts        INT NOT NULL DEFAULT 0,
    duration_ms     INT,
    artifacts       TEXT[]
);
CREATE INDEX IF NOT EXISTS ix_action_results_job_id ON action_results(job_id);

-- ---------- CONFIG / VERSIONING ------------------------------------------

CREATE TABLE IF NOT EXISTS extractors_run (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    extractor_name  TEXT NOT NULL,
    extractor_version TEXT,
    status          TEXT NOT NULL,
    facts_extracted INT,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    metadata_json   JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_extractors_run_run_id ON extractors_run(run_id);

CREATE TABLE IF NOT EXISTS field_bindings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    question_id     TEXT NOT NULL,
    tab_name        TEXT NOT NULL,
    control_type    TEXT NOT NULL,
    action          TEXT NOT NULL,
    match_name      TEXT,
    match_label     TEXT,
    match_top       INT,
    match_left_min  INT,
    match_left_max  INT,
    value_adapter   TEXT NOT NULL DEFAULT 'direct',
    binding_version TEXT NOT NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_field_bindings_run_id ON field_bindings(run_id);

-- ---------- AUDIT LOG -----------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    client_id       UUID NOT NULL REFERENCES clients(id),
    run_id          UUID REFERENCES runs(id),
    user_id         UUID REFERENCES users(id),
    event_type      TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       UUID,
    before_value    JSONB,
    after_value     JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address      INET
);
CREATE INDEX IF NOT EXISTS ix_audit_client_time ON audit_log(client_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_run_id ON audit_log(run_id);
CREATE INDEX IF NOT EXISTS ix_audit_event_type ON audit_log(event_type);

-- ---------- SYNC / CACHE / CLEANUP ---------------------------------------

CREATE TABLE IF NOT EXISTS sync_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    entity_type     TEXT NOT NULL,
    entity_id       UUID NOT NULL,
    operation       TEXT NOT NULL,
    payload_json    JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempted_at    TIMESTAMPTZ,
    synced_at       TIMESTAMPTZ,
    error_message   TEXT,
    retry_count     INT NOT NULL DEFAULT 0,
    max_retries     INT NOT NULL DEFAULT 3
);
CREATE INDEX IF NOT EXISTS ix_sync_queue_client_synced ON sync_queue(client_id, synced_at);
CREATE INDEX IF NOT EXISTS ix_sync_queue_pending
    ON sync_queue(synced_at) WHERE synced_at IS NULL;

CREATE TABLE IF NOT EXISTS sync_conflicts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    sync_queue_id   UUID REFERENCES sync_queue(id),
    entity_type     TEXT NOT NULL,
    entity_id       UUID NOT NULL,
    local_value     JSONB NOT NULL,
    remote_value    JSONB NOT NULL,
    resolution      TEXT,
    resolved_by     UUID REFERENCES users(id),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_sync_conflicts_client_id ON sync_conflicts(client_id);

CREATE TABLE IF NOT EXISTS cache_metadata (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    entity_type     TEXT NOT NULL,
    local_version   TEXT,
    remote_version  TEXT,
    last_checked_at TIMESTAMPTZ,
    last_updated_at TIMESTAMPTZ,
    CONSTRAINT uq_cache_client_entity UNIQUE(client_id, entity_type)
);
CREATE INDEX IF NOT EXISTS ix_cache_metadata_client_id ON cache_metadata(client_id);

CREATE TABLE IF NOT EXISTS file_deletion_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id),
    document_id     UUID REFERENCES documents(id),
    filename        TEXT NOT NULL,
    file_size_bytes INT,
    deletion_reason TEXT,
    deleted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_file_deletion_client_id ON file_deletion_log(client_id);

CREATE TABLE IF NOT EXISTS client_settings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    setting_key     TEXT NOT NULL,
    setting_value   JSONB NOT NULL,
    is_synced       BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_client_settings_key UNIQUE(client_id, setting_key)
);
CREATE INDEX IF NOT EXISTS ix_client_settings_sync ON client_settings(client_id, is_synced);

CREATE TABLE IF NOT EXISTS copy_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    rule_type       TEXT NOT NULL DEFAULT 'water_authority',
    authority_name  TEXT NOT NULL,
    annual_amount   DOUBLE PRECISION NOT NULL,
    notes           TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      TEXT,
    updated_at      TIMESTAMPTZ,
    CONSTRAINT uq_copy_rules_client_type_name UNIQUE(client_id, rule_type, authority_name)
);
CREATE INDEX IF NOT EXISTS ix_copy_rules_lookup ON copy_rules(client_id, rule_type, is_active);

CREATE TABLE IF NOT EXISTS copy_rules_audit (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    copy_rule_id        UUID NOT NULL REFERENCES copy_rules(id) ON DELETE CASCADE,
    change_type         TEXT NOT NULL,
    changed_by          TEXT NOT NULL,
    changed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    old_authority_name  TEXT,
    new_authority_name  TEXT,
    old_annual_amount   DOUBLE PRECISION,
    new_annual_amount   DOUBLE PRECISION,
    old_is_active       BOOLEAN,
    new_is_active       BOOLEAN,
    old_notes           TEXT,
    new_notes           TEXT
);
CREATE INDEX IF NOT EXISTS ix_copy_rules_audit_rule ON copy_rules_audit(copy_rule_id);

-- ---------- VECTOR MEMORY (Feature: pgvector 20-day TTL) -----------------
-- Requires: CREATE EXTENSION IF NOT EXISTS vector;  (see top of file)

CREATE TABLE IF NOT EXISTS memories (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT         NOT NULL,
    session_id  TEXT         NOT NULL,
    content     TEXT         NOT NULL,
    embedding   vector(1536) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ  NOT NULL   -- created_at + 20 days
);

CREATE INDEX IF NOT EXISTS ix_memories_user_id    ON memories(user_id);
CREATE INDEX IF NOT EXISTS ix_memories_expires_at ON memories(expires_at);

-- IVFFlat index for cosine ANN search (build after loading data if table is large)
-- CREATE INDEX ix_memories_embedding_cosine
--     ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
