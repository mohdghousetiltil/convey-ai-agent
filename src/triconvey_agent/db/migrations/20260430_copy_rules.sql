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
