from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ArtifactEvidence(BaseModel):
    source_file: str
    source_type: str | None = None
    page: int | None = None
    section: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    label: str | None = None
    snippet: str | None = None


class ArtifactFact(BaseModel):
    fact_id: str
    canonical_key: str
    category: str
    value: Any = None
    value_type: str = "unknown"
    confidence: float = 0.0
    status: Literal["supported", "conflicted", "incomplete", "unanswered"] = "supported"
    normalized_from: str | None = None
    source_extractors: list[str] = Field(default_factory=list)
    evidence: list[ArtifactEvidence] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)


class QuestionTarget(BaseModel):
    target_id: str
    tab_name: str | None = None
    field_index: int
    control_type: str | None = None
    yaml_auto_id: str | None = None
    yaml_name: str | None = None
    nearby_label: str | None = None
    dropdown_options: list[str] = Field(default_factory=list)
    mapped_canonical_keys: list[str] = Field(default_factory=list)
    candidate_answer: Any = None
    candidate_confidence: float = 0.0
    answer_status: Literal[
        "answered",
        "candidate_only",
        "needs_review",
        "not_found",
        "not_applicable",
        "conflicted",
    ] = "not_found"
    supporting_fact_ids: list[str] = Field(default_factory=list)
    evidence: list[ArtifactEvidence] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FillArtifact(BaseModel):
    artifact_version: str = "2.0"
    documents: list[dict[str, Any]] = Field(default_factory=list)
    facts: list[ArtifactFact] = Field(default_factory=list)
    question_targets: list[QuestionTarget] = Field(default_factory=list)
    review_queue: list[dict[str, Any]] = Field(default_factory=list)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
