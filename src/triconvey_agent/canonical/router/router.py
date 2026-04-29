"""Question router — Brain B.

Pure functions that read from a FactStore and produce AnswerObjects. The
router does not write back to the store; it only reads.

Public API
----------
answer_question(question, store, *, policy=None) -> AnswerObject
answer_all_questions(questions, store, *, policy=None) -> dict[str, AnswerObject]
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from collections.abc import Iterable

from triconvey_agent.ai.client import AIClient
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.schemas import (
    AnswerObject,
    AnswerStrategy,
    ClientPolicy,
    Conflict,
    Fact,
    Question,
    Source,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REVIEW_THRESHOLD = 0.75
_PLACEHOLDER_NUMBER_TOKENS = {
    "",
    "-",
    "--",
    "---",
    "n/a",
    "na",
    "nil",
    "none",
    "not applicable",
    "not available",
    "##",
}
_DATE_PATTERNS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y-%m-%d",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_override_for(
    question: Question, policy: ClientPolicy | None
) -> tuple[object, str] | None:
    """Return (override_value, reason) if a ClientOverride matches this question."""
    if policy is None:
        return None
    for override in policy.overrides:
        if override.question_id and override.question_id == question.id:
            return override.value, override.reason
        if override.question_label and override.question_label == question.label:
            return override.value, override.reason
    return None


def _gather_facts(
    question: Question, store: FactStoreImpl
) -> tuple[list[Fact], list[Conflict], list[str]]:
    """Resolve every fact_path on a question.

    Returns
    -------
    facts        : winning Fact for each path that resolved (in path order, may be shorter
                   than fact_paths if some paths had no fact / unresolved conflict).
    conflicts    : every Conflict record encountered (audit trail).
    paths_used   : the subset of fact_paths that produced a winner.
    """
    facts: list[Fact] = []
    conflicts: list[Conflict] = []
    paths_used: list[str] = []
    for path in question.fact_paths:
        winner, conflict = store.get(path)
        if conflict is not None:
            conflicts.append(conflict)
        if winner is not None:
            facts.append(winner)
            paths_used.append(path)
    return facts, conflicts, paths_used


def _gather_candidate_facts(
    question: Question, store: FactStoreImpl
) -> tuple[list[Fact], list[Conflict], list[str]]:
    """Gather every candidate fact for AI-backed questions.

    Unlike `_gather_facts`, this includes all facts at each path so the AI can
    see competing evidence instead of only the resolver winner.
    """
    facts: list[Fact] = []
    conflicts: list[Conflict] = []
    paths_used: list[str] = []
    seen_ids: set[int] = set()
    for path in question.fact_paths:
        winner, conflict = store.get(path)
        all_facts = store.get_all(path)
        if conflict is not None:
            conflicts.append(conflict)
        if winner is not None or all_facts:
            paths_used.append(path)
        for fact in all_facts:
            fact_id = id(fact)
            if fact_id in seen_ids:
                continue
            seen_ids.add(fact_id)
            facts.append(fact)
    return facts, conflicts, paths_used


def _evidence_from(facts: list[Fact]) -> list[Source]:
    out: list[Source] = []
    for f in facts:
        out.extend(f.sources)
    return out


def _aggregate_confidence(facts: list[Fact]) -> float:
    if not facts:
        return 0.0
    # Use the minimum — if any sub-fact is shaky, the answer is shaky.
    return min(f.confidence for f in facts)


def _extract_json_object(raw_text: str) -> dict | None:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return None
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(raw_text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _coerce_ai_value(value: object, expected_type: str | None) -> object | None:
    if value is None:
        return None
    if expected_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "y"}:
                return True
            if lowered in {"false", "no", "n"}:
                return False
        return None
    if expected_type == "number":
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value.replace(",", ""))
            except ValueError:
                return None
        return None
    if expected_type == "string":
        return str(value).strip() if str(value).strip() else None
    return value


def _looks_like_amount_question(question: Question) -> bool:
    token_space = " ".join(
        part
        for part in (
            question.id,
            question.label,
            question.description or "",
        )
        if part
    ).lower()
    return (
        " amount" in token_space
        or "_amount" in token_space
        or "total does not exceed" in token_space
    )


def _looks_like_date_question(question: Question) -> bool:
    if question.expected_type == "date":
        return True
    token_space = " ".join(
        part
        for part in (
            question.id,
            question.label,
            question.description or "",
        )
        if part
    ).lower()
    return " date" in token_space or token_space.endswith("_date")


def _normalize_amount_value(value: object) -> tuple[object, list[str]]:
    notes: list[str] = []
    if value is None:
        return None, notes
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}", notes
    if not isinstance(value, str):
        return value, notes

    cleaned = value.strip()
    normalized = cleaned.lower()
    if normalized in _PLACEHOLDER_NUMBER_TOKENS:
        notes.append(f"normalized numeric placeholder {cleaned!r} to '0.00'")
        return "0.00", notes
    return value, notes


def _normalize_date_value(value: object) -> tuple[object, list[str]]:
    notes: list[str] = []
    if value is None:
        return None, notes
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y"), notes
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y"), notes
    if not isinstance(value, str):
        return value, notes

    cleaned = value.strip()
    if not cleaned:
        return value, notes
    if cleaned.lower() in _PLACEHOLDER_NUMBER_TOKENS:
        return value, notes
    for pattern in _DATE_PATTERNS:
        try:
            parsed = datetime.strptime(cleaned, pattern)
            normalized = parsed.strftime("%d/%m/%Y")
            if normalized != cleaned:
                notes.append(f"normalized date {cleaned!r} to {normalized!r}")
            return normalized, notes
        except ValueError:
            continue
    return value, notes


def _normalize_answer_value(question: Question, value: object) -> tuple[object, list[str]]:
    normalized = value
    notes: list[str] = []
    if _looks_like_amount_question(question):
        normalized, amount_notes = _normalize_amount_value(normalized)
        notes.extend(amount_notes)
    if _looks_like_date_question(question):
        normalized, date_notes = _normalize_date_value(normalized)
        notes.extend(date_notes)
    return normalized, notes


def _value_requires_semantic_recovery(question: Question, value: object) -> bool:
    normalized_value, notes = _normalize_answer_value(question, value)
    if notes:
        return True
    if value is None:
        return True
    if question.options and value not in question.options:
        return True
    if isinstance(normalized_value, str):
        lowered = normalized_value.strip().lower()
        if _looks_like_amount_question(question) and lowered in _PLACEHOLDER_NUMBER_TOKENS:
            return True
        if _looks_like_date_question(question) and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", normalized_value.strip()):
            return True
    return False


def _attach_router_hints(answer: AnswerObject, **hints: object) -> AnswerObject:
    merged = dict(answer.presentation_hints)
    router_hints = dict(merged.get("router", {}))
    for key, value in hints.items():
        if value is not None:
            router_hints[key] = value
    if router_hints:
        merged["router"] = router_hints
    return answer.model_copy(update={"presentation_hints": merged})


def _postprocess_answer(question: Question, answer: AnswerObject) -> AnswerObject:
    normalized_value, notes = _normalize_answer_value(question, answer.value)
    if normalized_value == answer.value and not notes:
        return answer
    return _attach_router_hints(
        answer.model_copy(update={"value": normalized_value}),
        normalized_value=normalized_value,
        normalizations=notes,
    )


def _should_try_grounded_ai_recovery(question: Question, answer: AnswerObject, ai_client: AIClient | None) -> bool:
    if ai_client is None:
        return False
    if question.answer_strategy != AnswerStrategy.DETERMINISTIC:
        return False
    return answer.needs_review or _value_requires_semantic_recovery(question, answer.value)


def _prefer_recovery_answer(base: AnswerObject, recovered: AnswerObject) -> bool:
    if recovered.value is None:
        return False
    if not recovered.needs_review and base.needs_review:
        return True
    if base.value is None and recovered.value is not None and recovered.confidence >= base.confidence:
        return True
    if (
        recovered.confidence > base.confidence
        and len(recovered.review_reasons) < len(base.review_reasons)
    ):
        return True
    return False


def _build_grounded_ai_prompt(question: Question, facts: list[Fact]) -> str:
    evidence_lines: list[str] = []
    for idx, fact in enumerate(facts, start=1):
        source = fact.sources[0] if fact.sources else None
        evidence_lines.append(
            "\n".join(
                [
                    f"[{idx}] path: {fact.path}",
                    f"value: {fact.value!r}",
                    f"confidence: {fact.confidence:.2f}",
                    f"file: {source.file if source else ''}",
                    f"quote: {source.quote or '' if source else ''}",
                ]
            )
        )

    options_text = ", ".join(question.options or [])
    return (
        "You are answering one conveyancing form question from extracted evidence.\n"
        "Use ONLY the evidence provided below. Do not infer from general knowledge.\n"
        "If the answer is not explicit in the evidence, return null and needs_review=true.\n"
        "The quote must be copied exactly from one evidence quote below.\n"
        "Return ONLY strict JSON with keys: "
        'value, confidence, quote, source_file, reason, needs_review.\n\n'
        f"Question ID: {question.id}\n"
        f"Question Label: {question.label}\n"
        f"Expected Type: {question.expected_type or 'unknown'}\n"
        f"Allowed Options: {options_text or 'none'}\n"
        f"Description: {question.description or ''}\n\n"
        "Evidence:\n"
        f"{chr(10).join(evidence_lines)}\n"
    )


def _ai_answer_from_candidates(
    question: Question,
    facts: list[Fact],
    *,
    ai_client: AIClient,
    review_threshold: float,
) -> tuple[object | None, float, bool, list[str], list[Source], dict]:
    prompt = _build_grounded_ai_prompt(question, facts)
    try:
        response = ai_client.complete(prompt)
    except Exception as exc:
        evidence = _evidence_from(facts)
        return (
            None,
            0.0,
            True,
            [f"grounded_ai failed: {exc}"],
            evidence,
            {"ai_error": str(exc)},
        )
    parsed = _extract_json_object(response.raw_text)
    evidence = _evidence_from(facts)
    if parsed is None:
        return (
            None,
            0.0,
            True,
            ["grounded_ai response was not valid JSON"],
            evidence,
            {"raw_text": response.raw_text},
        )

    raw_value = parsed.get("value")
    value = _coerce_ai_value(raw_value, question.expected_type)
    quote = parsed.get("quote")
    source_file = str(parsed.get("source_file") or "")
    reason = str(parsed.get("reason") or "").strip()
    ai_confidence = parsed.get("confidence", 0.0)
    try:
        ai_confidence = float(ai_confidence)
    except (TypeError, ValueError):
        ai_confidence = 0.0
    ai_confidence = max(0.0, min(ai_confidence, 1.0))
    combined_confidence = min(ai_confidence, _aggregate_confidence(facts)) if facts else ai_confidence

    needs_review = bool(parsed.get("needs_review", False))
    review_reasons: list[str] = []
    matched_source = None
    if quote:
        for source in evidence:
            source_quote = source.quote or ""
            if quote == source_quote or quote in source_quote:
                if source_file and source.file != source_file:
                    continue
                matched_source = source
                break
        if matched_source is None:
            needs_review = True
            review_reasons.append("grounded_ai returned a quote that was not verified against candidate evidence")

    if raw_value is not None and value is None and question.expected_type in {"bool", "number"}:
        needs_review = True
        review_reasons.append(f"grounded_ai returned a value that could not be coerced to {question.expected_type}")

    if value is not None and question.options and value not in question.options:
        needs_review = True
        review_reasons.append("grounded_ai value is outside the allowed options")

    if value is not None and combined_confidence < review_threshold:
        needs_review = True
        review_reasons.append(
            f"confidence {combined_confidence:.2f} below review threshold {review_threshold:.2f}"
        )

    hints = {
        "ai_reason": reason,
        "ai_source_file": source_file or (matched_source.file if matched_source else ""),
        "ai_quote": quote,
        "raw_text": response.raw_text,
    }
    return value, combined_confidence, needs_review, review_reasons, evidence, hints


# ---------------------------------------------------------------------------
# Value transforms
# ---------------------------------------------------------------------------


def _apply_transform(
    question: Question, facts: list[Fact]
) -> tuple[object | None, list[str] | None]:
    """Combine the resolved facts into a single answer value.

    Returns
    -------
    (value, error_reasons)
        value         : the combined value, or None if the transform could not run
        error_reasons : list of reasons why combination failed, or None on success
    """
    transform = question.value_transform
    values = [f.value for f in facts]

    if not facts:
        return None, ["no facts found at any of the question's fact_paths"]

    if transform == "direct":
        if len(facts) == 1:
            return facts[0].value, None
        # Multiple facts but transform=direct → ambiguous, fall back to first
        return facts[0].value, None

    if transform == "negate":
        if len(facts) != 1:
            return None, [f"transform=negate requires exactly 1 fact, got {len(facts)}"]
        v = facts[0].value
        if not isinstance(v, bool):
            return None, [f"transform=negate requires bool value, got {type(v).__name__}"]
        return (not v), None

    if transform == "first_present":
        for v in values:
            if v not in (None, "", []):
                return v, None
        return None, ["no fact_path produced a non-empty value"]

    if transform == "any_true":
        bool_values = [v for v in values if isinstance(v, bool)]
        if not bool_values:
            return None, ["transform=any_true requires bool facts"]
        return any(bool_values), None

    if transform == "all_false":
        bool_values = [v for v in values if isinstance(v, bool)]
        if not bool_values:
            return None, ["transform=all_false requires bool facts"]
        return all(not v for v in bool_values), None

    return None, [f"unknown value_transform: {transform!r}"]


# ---------------------------------------------------------------------------
# Strategy handlers
# ---------------------------------------------------------------------------


def _answer_deterministic(
    question: Question, store: FactStoreImpl, *, review_threshold: float
) -> AnswerObject:
    facts, conflicts, paths_used = _gather_facts(question, store)
    value, errors = _apply_transform(question, facts)

    needs_review = False
    review_reasons: list[str] = []

    if errors:
        needs_review = True
        review_reasons.extend(errors)

    # Reject values that don't match the question's allowed options
    if value is not None and question.options:
        if value not in question.options:
            needs_review = True
            review_reasons.append(
                f"value {value!r} is not one of the allowed options ({len(question.options)} options)"
            )

    # Any unresolved conflict on a path used by this answer should trip review
    for c in conflicts:
        if c.resolution == "review_required":
            needs_review = True
            review_reasons.append(
                f"unresolved conflict on '{c.path}': {c.reason or 'no reason'}"
            )

    confidence = _aggregate_confidence(facts)
    if value is not None and confidence < review_threshold:
        needs_review = True
        review_reasons.append(
            f"confidence {confidence:.2f} below review threshold {review_threshold:.2f}"
        )

    return _postprocess_answer(
        question,
        AnswerObject(
        question_id=question.id,
        question_label=question.label,
        value=value,
        confidence=confidence,
        answer_strategy=AnswerStrategy.DETERMINISTIC,
        facts_used=paths_used,
        evidence=_evidence_from(facts),
        needs_review=needs_review,
        review_reasons=review_reasons,
        conflicts_seen=conflicts,
        ),
    )


def _answer_human_review(question: Question, store: FactStoreImpl) -> AnswerObject:
    facts, conflicts, paths_used = _gather_facts(question, store)
    return AnswerObject(
        question_id=question.id,
        question_label=question.label,
        value=None,
        confidence=0.0,
        answer_strategy=AnswerStrategy.HUMAN_REVIEW,
        facts_used=paths_used,
        evidence=_evidence_from(facts),
        needs_review=True,
        review_reasons=["question is registered as human_review (legally sensitive)"],
        conflicts_seen=conflicts,
    )


def _answer_grounded_ai(
    question: Question,
    store: FactStoreImpl,
    *,
    ai_client: AIClient | None,
    review_threshold: float,
) -> AnswerObject:
    """Answer a question with AI, constrained to extracted evidence."""
    facts, conflicts, paths_used = _gather_candidate_facts(question, store)
    if ai_client is None:
        return AnswerObject(
            question_id=question.id,
            question_label=question.label,
            value=None,
            confidence=0.0,
            answer_strategy=AnswerStrategy.GROUNDED_AI,
            facts_used=paths_used,
            evidence=_evidence_from(facts),
            needs_review=True,
            review_reasons=["grounded_ai strategy requested but no AI client was supplied"],
            conflicts_seen=conflicts,
        )

    if not facts:
        return AnswerObject(
            question_id=question.id,
            question_label=question.label,
            value=None,
            confidence=0.0,
            answer_strategy=AnswerStrategy.GROUNDED_AI,
            facts_used=paths_used,
            evidence=[],
            needs_review=True,
            review_reasons=["grounded_ai strategy has no candidate evidence"],
            conflicts_seen=conflicts,
        )

    value, confidence, needs_review, review_reasons, evidence, hints = _ai_answer_from_candidates(
        question,
        facts,
        ai_client=ai_client,
        review_threshold=review_threshold,
    )

    for c in conflicts:
        if c.resolution == "review_required":
            needs_review = True
            review_reasons.append(
                f"unresolved conflict on '{c.path}': {c.reason or 'no reason'}"
            )

    return _postprocess_answer(
        question,
        AnswerObject(
        question_id=question.id,
        question_label=question.label,
        value=value,
        confidence=confidence,
        answer_strategy=AnswerStrategy.GROUNDED_AI,
        facts_used=paths_used,
        evidence=evidence,
        needs_review=needs_review,
        review_reasons=review_reasons,
        conflicts_seen=conflicts,
        presentation_hints=hints,
        ),
    )


def _answer_policy_default(
    question: Question,
    policy: ClientPolicy | None,
) -> AnswerObject:
    if policy is None:
        return AnswerObject(
            question_id=question.id,
            question_label=question.label,
            value=None,
            confidence=0.0,
            answer_strategy=AnswerStrategy.POLICY_DEFAULT,
            needs_review=True,
            review_reasons=[
                "policy_default question with no ClientPolicy supplied"
            ],
        )
    # The override branch is handled before the strategy switch in
    # answer_question, so reaching here means there is no override.
    # Without explicit policy data we still cannot fabricate a value.
    return AnswerObject(
        question_id=question.id,
        question_label=question.label,
        value=None,
        confidence=0.0,
        answer_strategy=AnswerStrategy.POLICY_DEFAULT,
        needs_review=True,
        review_reasons=[
            "policy_default question has no matching ClientOverride or ClientQuestionRule"
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def answer_question(
    question: Question,
    store: FactStoreImpl,
    *,
    policy: ClientPolicy | None = None,
    review_threshold: float | None = None,
    ai_client: AIClient | None = None,
) -> AnswerObject:
    """Produce an AnswerObject for one Question.

    Client overrides win unconditionally — when an override exists for
    this question, the answer is taken from the override and confidence
    is forced to 1.0. The override path is the only place where the
    answer can be set without a Fact backing it.
    """
    threshold = review_threshold
    if threshold is None and policy is not None:
        threshold = policy.preferences.default_review_threshold
    if threshold is None:
        threshold = DEFAULT_REVIEW_THRESHOLD

    # 1) Hard override — bypasses every strategy
    override = _client_override_for(question, policy)
    if override is not None:
        value, reason = override
        return AnswerObject(
            question_id=question.id,
            question_label=question.label,
            value=value,
            confidence=1.0,
            answer_strategy=question.answer_strategy,
            facts_used=[],
            evidence=[],
            needs_review=False,
            review_reasons=[],
            conflicts_seen=[],
            presentation_hints={"client_override_reason": reason},
        )

    # 2) Strategy dispatch
    strategy = question.answer_strategy
    if strategy == AnswerStrategy.DETERMINISTIC:
        answer = _answer_deterministic(question, store, review_threshold=threshold)
        if _should_try_grounded_ai_recovery(question, answer, ai_client):
            recovered = _answer_grounded_ai(
                question,
                store,
                ai_client=ai_client,
                review_threshold=threshold,
            )
            recovered = _attach_router_hints(
                recovered,
                recovered_from="deterministic",
                baseline_value=answer.value,
                baseline_needs_review=answer.needs_review,
            )
            if _prefer_recovery_answer(answer, recovered):
                return recovered
            answer = _attach_router_hints(answer, grounded_ai_recovery_attempted=True)
        return answer
    if strategy == AnswerStrategy.HUMAN_REVIEW:
        return _answer_human_review(question, store)
    if strategy == AnswerStrategy.GROUNDED_AI:
        return _answer_grounded_ai(
            question,
            store,
            ai_client=ai_client,
            review_threshold=threshold,
        )
    if strategy == AnswerStrategy.POLICY_DEFAULT:
        return _answer_policy_default(question, policy)

    # Defensive fallback
    return AnswerObject(
        question_id=question.id,
        question_label=question.label,
        value=None,
        confidence=0.0,
        answer_strategy=strategy,
        needs_review=True,
        review_reasons=[f"unsupported answer_strategy: {strategy!r}"],
    )


def answer_all_questions(
    questions: Iterable[Question],
    store: FactStoreImpl,
    *,
    policy: ClientPolicy | None = None,
    review_threshold: float | None = None,
    ai_client: AIClient | None = None,
) -> dict[str, AnswerObject]:
    """Run the router across an entire question registry."""
    return {
        q.id: answer_question(
            q,
            store,
            policy=policy,
            review_threshold=review_threshold,
            ai_client=ai_client,
        )
        for q in questions
    }
