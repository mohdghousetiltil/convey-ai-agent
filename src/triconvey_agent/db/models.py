"""SQLAlchemy ORM models mirroring the Postgres schema.

All models are 1:1 with the CREATE TABLE statements applied to the DB.
Keep this file in lockstep with `schema.sql`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from triconvey_agent.db.session import Base

# -------- Helpers -----------------------------------------------------------


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def _ts_now() -> Mapped[datetime]:
    return mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


def _ts_optional() -> Mapped[datetime | None]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=True)


# -------- Clients / Users / Sessions ----------------------------------------


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    license_key: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()
    last_synced_at: Mapped[datetime | None] = _ts_optional()
    sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))

    users: Mapped[list["User"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("client_id", "email", name="uq_users_client_email"),
        Index("ix_users_client_id", "client_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'reviewer'"))
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()
    last_login_at: Mapped[datetime | None] = _ts_optional()
    # OAuth fields (null for password-based users)
    oauth_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_subject: Mapped[str | None] = mapped_column(Text, nullable=True)

    client: Mapped[Client] = relationship(back_populates="users")


class SessionToken(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_token_hash", "token_hash"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _ts_now()
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


# -------- Matters / Runs / Documents ----------------------------------------


class Matter(Base):
    __tablename__ = "matters"
    __table_args__ = (
        UniqueConstraint("client_id", "matter_ref", name="uq_matters_client_ref"),
        Index("ix_matters_client_status", "client_id", "status"),
        Index("ix_matters_volume_folio", "volume_folio"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    matter_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    volume_folio: Mapped[str | None] = mapped_column(Text, nullable=True)
    council_area: Mapped[str | None] = mapped_column(Text, nullable=True)
    water_authority: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_run_at: Mapped[datetime | None] = _ts_optional()


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_client_status", "client_id", "status"),
        Index("ix_runs_matter_id", "matter_id"),
        Index("ix_runs_is_synced", "is_synced"),
    )

    # NOTE: schema declares `runs.id UUID`. We generate UUIDs for new runs; if
    # you have legacy string run_ids, migrate them or add a `slug` column.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matters.id"), nullable=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    use_ai_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    model: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'gpt-4.1-mini'"))
    document_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_facts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()
    completed_at: Mapped[datetime | None] = _ts_optional()
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    form_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("form_templates.id"), nullable=True
    )
    is_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    synced_at: Mapped[datetime | None] = _ts_optional()
    local_only: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_reasons: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    uploaded_at: Mapped[datetime] = _ts_now()
    marked_for_deletion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    deleted_at: Mapped[datetime | None] = _ts_optional()


# -------- Facts / Sources / Conflicts ---------------------------------------


class Fact(Base):
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_run_path", "run_id", "path"),
        Index("ix_facts_needs_sync", "needs_sync"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    value_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extractor: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_winning: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    created_at: Mapped[datetime] = _ts_now()
    needs_sync: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    last_synced_at: Mapped[datetime | None] = _ts_optional()


class FactSource(Base):
    __tablename__ = "fact_sources"
    __table_args__ = (Index("ix_fact_sources_fact_id", "fact_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True
    )
    file: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    extractor_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class FactConflict(Base):
    __tablename__ = "fact_conflicts"
    __table_args__ = (Index("ix_fact_conflicts_run_path", "run_id", "path"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    winning_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    authority_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("authority_rules.id"), nullable=True
    )
    created_at: Mapped[datetime] = _ts_now()


# -------- Questions / Answers / Evidence ------------------------------------


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (Index("ix_questions_is_active", "is_active"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tab: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    expected_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    fact_paths: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    options: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_transform: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'direct'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("run_id", "question_id", name="uq_answers_run_question"),
        Index("ix_answers_run_id", "run_id"),
        Index("ix_answers_needs_sync", "needs_sync"),
        Index("ix_answers_needs_review", "needs_review"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(
        Text, ForeignKey("questions.id"), nullable=False
    )
    tab: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    expected_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"))
    facts_used: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    human_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    human_value_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    human_edited_at: Mapped[datetime | None] = _ts_optional()
    human_edited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    review_reasons: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    presentation_hints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    needs_sync: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    last_synced_at: Mapped[datetime | None] = _ts_optional()
    is_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))


class AnswerEvidence(Base):
    __tablename__ = "answer_evidence"
    __table_args__ = (Index("ix_answer_evidence_answer_id", "answer_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answers.id", ondelete="CASCADE"), nullable=False
    )
    fact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id"), nullable=True
    )
    file: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )


class AIReview(Base):
    __tablename__ = "ai_reviews"
    __table_args__ = (Index("ix_ai_reviews_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answers.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggestion_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime] = _ts_now()
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    is_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))


# -------- Autofill Jobs / Action Results ------------------------------------


class AutofillJob(Base):
    __tablename__ = "autofill_jobs"
    __table_args__ = (Index("ix_autofill_jobs_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    skip_review_gate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    triconvey_exe: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_now()
    started_at: Mapped[datetime | None] = _ts_optional()
    completed_at: Mapped[datetime | None] = _ts_optional()
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_filled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_verified: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_skipped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_pending_review: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))


class ActionResult(Base):
    __tablename__ = "action_results"
    __table_args__ = (Index("ix_action_results_job_id", "job_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autofill_jobs.id", ondelete="CASCADE"), nullable=False
    )
    answer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answers.id"), nullable=True
    )
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    field_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    actual_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifacts: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)


# -------- Configuration / Versioning ----------------------------------------


class ExtractorsRun(Base):
    __tablename__ = "extractors_run"
    __table_args__ = (Index("ix_extractors_run_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    extractor_name: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    facts_extracted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = _ts_now()
    completed_at: Mapped[datetime | None] = _ts_optional()
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class AuthorityRuleRow(Base):
    __tablename__ = "authority_rules"
    __table_args__ = (Index("ix_authority_rules_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    path_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    authoritative_extractors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    on_unresolved: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime] = _ts_now()


class FieldBinding(Base):
    __tablename__ = "field_bindings"
    __table_args__ = (Index("ix_field_bindings_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    tab_name: Mapped[str] = mapped_column(Text, nullable=False)
    control_type: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    match_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_top: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_left_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_left_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_adapter: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'direct'"))
    binding_version: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime] = _ts_now()


class FormTemplate(Base):
    __tablename__ = "form_templates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tabs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at: Mapped[datetime] = _ts_now()


# -------- Audit / Sync / Cache ---------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_client_time", "client_id", "occurred_at"),
        Index("ix_audit_run_id", "run_id"),
        Index("ix_audit_event_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    before_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = _ts_now()
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)


class SyncQueue(Base):
    __tablename__ = "sync_queue"
    __table_args__ = (Index("ix_sync_queue_client_synced", "client_id", "synced_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts_now()
    attempted_at: Mapped[datetime | None] = _ts_optional()
    synced_at: Mapped[datetime | None] = _ts_optional()
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))


class SyncConflict(Base):
    __tablename__ = "sync_conflicts"
    __table_args__ = (Index("ix_sync_conflicts_client_id", "client_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    sync_queue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sync_queue.id"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    local_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    remote_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = _ts_optional()
    created_at: Mapped[datetime] = _ts_now()


class CacheMetadata(Base):
    __tablename__ = "cache_metadata"
    __table_args__ = (
        UniqueConstraint("client_id", "entity_type", name="uq_cache_client_entity"),
        Index("ix_cache_metadata_client_id", "client_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    local_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = _ts_optional()
    last_updated_at: Mapped[datetime | None] = _ts_optional()


class FileDeletionLog(Base):
    __tablename__ = "file_deletion_log"
    __table_args__ = (Index("ix_file_deletion_client_id", "client_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime] = _ts_now()


class ClientSetting(Base):
    __tablename__ = "client_settings"
    __table_args__ = (
        UniqueConstraint("client_id", "setting_key", name="uq_client_settings_key"),
        Index("ix_client_settings_sync", "client_id", "is_synced"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    setting_key: Mapped[str] = mapped_column(Text, nullable=False)
    setting_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    is_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    updated_at: Mapped[datetime] = _ts_now()


# -------- Public registry --------------------------------------------------


ALL_MODELS: tuple[type[Base], ...] = (
    Client,
    User,
    SessionToken,
    Matter,
    Run,
    Document,
    Fact,
    FactSource,
    FactConflict,
    Question,
    Answer,
    AnswerEvidence,
    AIReview,
    AutofillJob,
    ActionResult,
    ExtractorsRun,
    AuthorityRuleRow,
    FieldBinding,
    FormTemplate,
    AuditLog,
    SyncQueue,
    SyncConflict,
    CacheMetadata,
    FileDeletionLog,
    ClientSetting,
)
