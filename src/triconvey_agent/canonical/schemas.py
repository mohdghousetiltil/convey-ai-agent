"""Canonical schemas for the 5-brain architecture (step 1 — types only).

This file defines every data shape that flows between brains. There is NO
logic here — only types and validation. Implementation comes in later steps.

Data flow:

    raw documents
        |
        v
    Brain A (extractors)         ──>  emit Fact objects to FactStore
        |
        v
    FactStore (canonical truth)  ──>  query by path, returns Fact + Conflict info
        |
        v
    Brain B (Question Router)    ──>  produces AnswerObject per Question
        |
        v
    AnswerObject (objective)
        |
        v
    Brain C (Policy Overlay)     ──>  rewrites presentation, never the value
        |
        v
    AnswerObject (presentation-final)
        |
        v
    Brain D (Field Mapper)       ──>  produces FormActionPlan
        |
        v
    Brain E (Desktop Executor)   ──>  pywinauto fills, verifies, reports
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ===========================================================================
# Brain A — Evidence Layer
# ===========================================================================


class Source(BaseModel):
    """A single piece of evidence backing a Fact.

    Every Source ties a Fact back to one specific document, ideally with a
    page number and the verbatim quote that proves the value. quote_verified
    is set True only after the quote has been confirmed as a real substring
    of the document's raw text (anti-hallucination guard).
    """

    file: str
    """Filename of the source document (matches Document.filename)."""

    page: int | None = None
    """Page number within that document, when known."""

    quote: str | None = None
    """Verbatim text from the document that proves the value."""

    quote_verified: bool = False
    """True iff the quote has been confirmed as a substring of raw_text."""

    extractor_note: str | None = None
    """Optional free-text note from the extractor (regex matched, OCR cell, etc.)."""


class Fact(BaseModel):
    """A single asserted piece of information about the property.

    Multiple Facts can exist at the same path — that represents a conflict
    between sources, which the FactStore detects and Brain B's Question
    Router resolves using the AuthorityRules below.

    The path is a dot-separated key in a flat namespace, e.g.:
        "title.volume_folio"
        "title.registered_proprietor"
        "rates.council.annual_amount"
        "services.electricity.connected"
        "planning.bushfire_prone"
        "insurance.home.policy_number"
    """

    path: str
    """Dot-separated canonical path. Free-form, no fixed schema by design."""

    value: Any
    """The asserted value (bool, str, number, list, dict — extractor's choice)."""

    confidence: float = Field(ge=0.0, le=1.0)
    """How confident the extractor is in this value, 0.0–1.0."""

    sources: list[Source] = Field(default_factory=list)
    """Evidence — at least one Source unless this is an inferred fact."""

    extractor: str
    """
    Identifier of who produced this Fact, e.g.:
      "rule:vendor_form_v2"
      "rule:vic_title_v1"
      "ai:doc_extractor:vendor_form"
      "ai:summarizer"
    Used by AuthorityRules to break ties.
    """

    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    """When this fact was produced (used for recency tie-breaking)."""

    notes: str | None = None
    """Optional human-readable note about how this value was derived."""


class DocInfo(BaseModel):
    """A summary of one ingested document, kept in the FactStore for traceability.

    Even unclassified documents appear here so the system can prove every
    file was seen and is accounted for in the audit trail.
    """

    file: str
    document_type: str
    """Best-guess document type (vendor_form, vic_title, planning_certificate, ...)."""

    classification_confidence: float = 0.0
    page_count: int | None = None
    char_count: int | None = None
    classifier: str = "unknown"
    """Which classifier produced the document_type ('rule:keywords' / 'ai:summarizer')."""


class Conflict(BaseModel):
    """Two or more Facts disagree on the same path.

    The Question Router consults AuthorityRules to choose a winning Fact;
    if no rule resolves it, the conflict is escalated to the review queue.
    """

    path: str
    facts: list[Fact]
    """All disagreeing facts at this path."""

    resolution: Literal["unresolved", "authority_wins", "review_required"] = "unresolved"
    winning_fact: Fact | None = None
    reason: str | None = None
    """Why this resolution was chosen — for the audit trail."""


class FactStore(BaseModel):
    """Append-only store of all Facts asserted by all extractors.

    Implementation note: this is a pydantic-validated container, but the
    real working store will likely live in `canonical/facts/store.py` with
    indexing helpers. This schema defines what gets serialized to disk.
    """

    facts: dict[str, list[Fact]] = Field(default_factory=dict)
    """Path → list of Facts. Multiple facts per path = conflict candidates."""

    documents: list[DocInfo] = Field(default_factory=list)
    """Every ingested file, classified or not."""

    extractors_run: list[str] = Field(default_factory=list)
    """Names of extractors that have written to this store."""


# ===========================================================================
# Authority Rules — used by Brain B to resolve conflicts at query time
# ===========================================================================


class AuthorityRule(BaseModel):
    """Per-path rule for which extractor (or doc type) is authoritative.

    Example:
        AuthorityRule(
            path_pattern="planning.*",
            authoritative_extractors=[
                "rule:planning_certificate",
                "ai:doc_extractor:planning_certificate",
                "rule:council_certificate",
                "rule:vendor_form_v2",
            ],
            on_unresolved="review",
        )

    Means: for any fact under planning.*, prefer the planning certificate
    extractor over the vendor form. If two equally authoritative facts
    disagree, escalate to human review.
    """

    path_pattern: str
    """Glob-style path prefix, e.g. 'planning.*' or 'rates.council.*'."""

    authoritative_extractors: list[str]
    """Ordered list — index 0 wins ties, index 1 is fallback, etc."""

    on_unresolved: Literal["review", "highest_confidence", "first_seen"] = "review"
    """What to do when authoritative_extractors cannot break the tie."""

    note: str | None = None


# ===========================================================================
# Brain B — Question Engine
# ===========================================================================


class AnswerStrategy(str, Enum):
    """How a question's answer should be produced."""

    DETERMINISTIC = "deterministic"
    """Pull a single high-confidence fact directly from the FactStore."""

    GROUNDED_AI = "grounded_ai"
    """Send the question + relevant document snippets to AI, require quote."""

    HUMAN_REVIEW = "human_review"
    """Cannot answer automatically — escalate to the review queue."""

    POLICY_DEFAULT = "policy_default"
    """Answer is dictated entirely by client policy (e.g. standard wording)."""


class Question(BaseModel):
    """A single TriConvey question registered with the router.

    The router has one Question entry per form question. Multiple questions
    can map to the same fact paths — that's how rates outgoings 1, 2, 3, …
    all draw from `rates.outgoings[]`.
    """

    id: str
    """Stable identifier (e.g. 'sec32_1.1_outgoing_1_amount')."""

    tab: str
    label: str

    fact_paths: list[str] = Field(default_factory=list)
    """Canonical paths the router consults first."""

    answer_strategy: AnswerStrategy = AnswerStrategy.DETERMINISTIC

    value_transform: Literal[
        "direct", "negate", "first_present", "any_true", "all_false"
    ] = "direct"
    """How to combine fact_paths into the answer value:

    direct          single path → return its value as-is
    negate          single bool path → return NOT value
    first_present   walk paths in order, return the first non-None value
    any_true        bool paths → True if any is True
    all_false       bool paths → True if every one is False  (e.g. "tick if NOT connected")
    """

    options: list[str] | None = None
    """Allowed values for dropdown questions."""

    expected_type: Literal["bool", "string", "number", "date", "list"] | None = None
    description: str | None = None
    """Free-text legal context shown to AI when strategy is GROUNDED_AI."""


class AnswerObject(BaseModel):
    """The objective answer to one Question, before client policy is applied.

    AnswerObjects are the contract between Brain B and Brain C. Brain C is
    allowed to set presentation_hints and call back to .with_presentation()
    but MUST NOT mutate value, facts_used, or evidence.
    """

    question_id: str
    question_label: str

    value: Any | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    answer_strategy: AnswerStrategy = AnswerStrategy.DETERMINISTIC

    facts_used: list[str] = Field(default_factory=list)
    """Fact paths that were consulted in producing this answer."""

    evidence: list[Source] = Field(default_factory=list)
    """Quotes shown to the user as proof — copied from the winning Facts."""

    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)

    conflicts_seen: list[Conflict] = Field(default_factory=list)
    """Any conflicts encountered while answering — kept for the audit trail."""

    # ----- Presentation layer (Brain C may set, may not delete) -----

    presentation_hints: dict[str, Any] = Field(default_factory=dict)
    """
    Brain C-controlled hints, e.g.:
        {"prefer": "attached_certificate"}
        {"text_style": "terse"}
        {"checkbox_only": True}
    Brain D consumes these to choose a UI action variant.
    """

    presentation_value: Any | None = None
    """
    Optional alternate value Brain C may want shown in the UI (e.g. a
    rewritten phrase). When None, Brain D uses .value directly.
    Brain C MUST NOT modify .value itself — only this field.
    """


# ===========================================================================
# Brain C — Client Policy
# ===========================================================================


class ClientPreferences(BaseModel):
    """Style and presentation defaults — applied universally for one client."""

    text_style: Literal["terse", "standard", "verbose"] = "standard"
    prefer_attached_certificate: bool = True
    """When a question can be answered by 'see attached cert' instead of
    typing the details, do so."""
    default_review_threshold: float = 0.75
    """Below this confidence, route to human review even if AI returned a value."""
    standard_no_notice_text: str | None = None
    """Boilerplate text to use for 'no notices/orders' style questions."""
    standard_no_easement_failure_text: str | None = None


class ClientOverride(BaseModel):
    """A hard override that always wins, regardless of evidence.

    Use sparingly — every override is a place where the system stops
    reading documents and just types what the client said.
    """

    question_id: str | None = None
    question_label: str | None = None
    """Either question_id OR question_label must be set."""

    value: Any
    reason: str
    """Why this override exists — REQUIRED for the audit trail."""


class ClientQuestionRule(BaseModel):
    """Per-question handling preference (presentation, not value)."""

    question_id: str | None = None
    question_label: str | None = None

    prefer: Literal["attached_certificate", "typed_particulars", "checkbox_only"] | None = None
    """How to present this question's answer."""

    text_template: str | None = None
    """Optional template for rewriting the answer's display text."""


class ClientPolicy(BaseModel):
    """Aggregated client policy — loaded from 3 yaml files."""

    preferences: ClientPreferences = Field(default_factory=ClientPreferences)
    overrides: list[ClientOverride] = Field(default_factory=list)
    question_rules: list[ClientQuestionRule] = Field(default_factory=list)


# ===========================================================================
# Brain D — Field Mapping
# ===========================================================================


class FormAction(BaseModel):
    """One UI action to perform on the TriConvey desktop form.

    Brain D produces a flat list of these from the AnswerObjects + the
    YAML field maps. Brain E (pywinauto) executes them in order.
    """

    question_id: str

    field_id: str
    """Identifier from the YAML field map (e.g. 'sec32_1.1_amount_textbox')."""

    action: Literal["set_text", "set_checkbox", "select_dropdown", "click", "skip"]
    payload: Any | None = None
    """The value to set, or None for click/skip."""

    expected_after: Any | None = None
    """What the field should contain after the action — used by Brain E to verify."""

    needs_review_first: bool = False
    """Block execution until a human approves."""

    source_answer_confidence: float = 0.0
    """Carries the AnswerObject confidence forward for the review gate."""

    intent_category: Literal["navigation", "search", "policy_tick", "exact_entry", "derived_entry", "selection", "skip"] = "exact_entry"
    """High-level agent intent used for narration and operator visibility."""

    source_kind: Literal["policy_default", "extracted_fact", "computed_policy", "grounded_ai", "unknown"] = "unknown"
    """Where the agent's instruction came from."""

    intent_summary: str | None = None
    """Short human-readable explanation of why this field is being handled."""


class FormActionPlan(BaseModel):
    """The full UI plan Brain E executes."""

    actions: list[FormAction] = Field(default_factory=list)
    review_gate_required: bool = False
    """If True, Brain E pauses for human approval before executing any action."""


# ===========================================================================
# Brain E — Execution Result
# ===========================================================================


class ActionResult(BaseModel):
    """Outcome of one FormAction execution."""

    action: FormAction
    status: Literal["filled", "verified", "skipped", "failed", "pending_review"]
    actual_value: Any | None = None
    error: str | None = None
    attempts: int = 1
    locator_strategy: str | None = None
    verification_mode: str | None = None
    duration_ms: int | None = None
    artifacts: list[str] = Field(default_factory=list)


class ExecutionReport(BaseModel):
    """Brain E's final report, written next to answered_triconvey_tab.json."""

    results: list[ActionResult] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    total_filled: int = 0
    total_verified: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    total_pending_review: int = 0
    diagnostics_dir: str | None = None
    event_log_path: str | None = None
    debug_log_path: str | None = None
    brain_e_debug_log_path: str | None = None
    brain_e_learning_log_path: str | None = None
    brain_e_learning_summary_path: str | None = None
    brain_e_learning_profile_path: str | None = None
    session_fingerprint: dict[str, Any] = Field(default_factory=dict)
    preflight_checks: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
