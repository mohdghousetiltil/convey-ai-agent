from __future__ import annotations

import re
from collections.abc import Iterable

from triconvey_agent.ingest.normalizer import collapse_snippet, normalize_label
from triconvey_agent.schemas.documents import Document, SourceSpan
from triconvey_agent.schemas.extracted import Evidence, FieldValue

QUESTION_STARTERS = {
    "are",
    "can",
    "could",
    "did",
    "do",
    "does",
    "has",
    "have",
    "how",
    "if",
    "is",
    "what",
    "when",
    "where",
    "who",
    "why",
    "will",
    "would",
}


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def iter_clean_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def page_text(doc: Document, page_number: int) -> str:
    for page in doc.pages:
        if page.page_number == page_number:
            return page.normalized_text or page.text or ""
    return ""


def iter_page_text(doc: Document) -> Iterable[tuple[int, str]]:
    if doc.pages:
        for page in doc.pages:
            yield page.page_number, page.normalized_text or page.text or ""
        return

    yield 1, doc.normalized_text or doc.raw_text or ""


def looks_like_section_heading(line: str) -> bool:
    stripped = line.strip()
    lowered = stripped.lower()
    if lowered.startswith("section "):
        return True
    if stripped.isupper() and len(stripped.split()) <= 8:
        return True
    return False


def looks_like_question_start(line: str) -> bool:
    tokens = normalize_label(line).split()
    if not tokens:
        return False
    return tokens[0] in QUESTION_STARTERS


def normalize_question_key(question: str) -> str:
    return normalize_label(question).replace(" ", "_")


def question_matches(candidate: str, expected: str) -> bool:
    candidate_norm = normalize_label(candidate)
    expected_norm = normalize_label(expected)
    if not candidate_norm or not expected_norm:
        return False
    if candidate_norm == expected_norm:
        return True
    if candidate_norm in expected_norm or expected_norm in candidate_norm:
        return True

    candidate_tokens = set(candidate_norm.split())
    expected_tokens = set(expected_norm.split())
    if len(expected_tokens) >= 4 and expected_tokens.issubset(candidate_tokens):
        return True
    return False


def iter_question_answer_pairs(
    text: str,
    *,
    max_question_lines: int = 4,
    max_answer_lines: int = 3,
) -> Iterable[tuple[str, str]]:
    lines = iter_clean_lines(text)
    i = 0
    while i < len(lines):
        if not looks_like_question_start(lines[i]):
            i += 1
            continue

        question_parts = [lines[i]]
        question_end = i
        while "?" not in question_parts[-1] and question_end + 1 < len(lines) and len(question_parts) < max_question_lines:
            question_end += 1
            question_parts.append(lines[question_end])

        question = compact_spaces(" ".join(question_parts))
        if "?" not in question:
            i += 1
            continue

        answer_parts: list[str] = []
        cursor = question_end + 1
        while cursor < len(lines) and len(answer_parts) < max_answer_lines:
            line = lines[cursor]
            if looks_like_section_heading(line):
                break
            if answer_parts and looks_like_question_start(line):
                break
            if answer_parts and "?" in line:
                break
            answer_parts.append(line)
            cursor += 1
            if cursor < len(lines) and answer_parts and looks_like_question_start(lines[cursor]):
                break

        answer = compact_spaces(" ".join(answer_parts))
        if answer:
            yield question, answer

        i = max(cursor, question_end + 1)


def collect_value_after_label(
    text: str,
    label: str,
    *,
    max_label_lines: int = 4,
    max_value_lines: int = 1,
) -> str | None:
    lines = iter_clean_lines(text)
    target = normalize_label(label)

    for start_index in range(len(lines)):
        assembled_parts: list[str] = []
        for end_index in range(start_index, min(len(lines), start_index + max_label_lines)):
            assembled_parts.append(lines[end_index])
            assembled = compact_spaces(" ".join(assembled_parts))
            if normalize_label(assembled) != target:
                continue

            value_parts: list[str] = []
            cursor = end_index + 1
            while cursor < len(lines) and len(value_parts) < max_value_lines:
                line = lines[cursor]
                if looks_like_section_heading(line):
                    break
                if value_parts and looks_like_question_start(line):
                    break
                if value_parts and normalize_label(line).startswith("section"):
                    break
                value_parts.append(line)
                cursor += 1
                if cursor < len(lines) and value_parts and looks_like_question_start(lines[cursor]):
                    break

            value = compact_spaces(" ".join(value_parts))
            return value or None

    return None


def make_field(
    *,
    value: object,
    document: Document,
    extractor: str,
    normalized_key: str | None = None,
    page: int | None = None,
    section: str | None = None,
    snippet: str | None = None,
    label: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    confidence: float = 0.95,
) -> FieldValue:
    evidence = [
        Evidence(
            source_file=document.filename,
            source_type=document.document_type,
            span=SourceSpan(page=page, section=section, line_start=line_start, line_end=line_end, label=label),
            snippet=collapse_snippet(snippet or ""),
            label=label,
        )
    ]
    return FieldValue(
        value=value,
        confidence=confidence,
        extractor=extractor,
        normalized_key=normalized_key,
        evidence=evidence,
    )
