from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from triconvey_agent.schemas.documents import DocumentType, SourceSpan


class Evidence(BaseModel):
    source_file: str
    source_type: DocumentType | None = None
    span: SourceSpan | None = None
    snippet: str | None = None
    label: str | None = None


class ConflictItem(BaseModel):
    source_file: str
    source_type: DocumentType | None = None
    value: Any
    reason: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class FieldValue(BaseModel):
    value: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extractor: str | None = None
    normalized_key: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    requires_review: bool = False


class Section32Questions(BaseModel):
    yes_questions: dict[str, FieldValue] = Field(default_factory=dict)
    no_questions: dict[str, FieldValue] = Field(default_factory=dict)
    unanswered_or_not_found: dict[str, FieldValue] = Field(default_factory=dict)
    conflicts: dict[str, list[ConflictItem]] = Field(default_factory=dict)


class FinalExtraction(BaseModel):
    vendor_core: dict[str, FieldValue] = Field(default_factory=dict)
    trustee: dict[str, FieldValue] = Field(default_factory=dict)
    property_details: dict[str, FieldValue] = Field(default_factory=dict)
    services_connected: dict[str, FieldValue] = Field(default_factory=dict)
    rates_taxes_charges: dict[str, FieldValue] = Field(default_factory=dict)
    planning_building_permits: dict[str, FieldValue] = Field(default_factory=dict)
    vic_title_extract: dict[str, FieldValue] = Field(default_factory=dict)
    section32_questions: Section32Questions = Field(default_factory=Section32Questions)
    skipped_sections: list[str] = Field(default_factory=list)
    source_summary: dict[str, Any] = Field(default_factory=dict)
    review_summary: dict[str, Any] = Field(default_factory=dict)
    additional_titles: list[dict[str, Any]] = Field(default_factory=list)
    ai_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    # AI-generated summary of every uploaded PDF (document_category, date, key_facts …)
    # Populated when --use-ai-extract is passed; empty list otherwise.
    source_documents: list[dict[str, Any]] = Field(default_factory=list)
