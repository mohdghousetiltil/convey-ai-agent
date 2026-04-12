from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from triconvey_agent.extractors.form_stopwords import SOFT_STOP_LABELS
from triconvey_agent.ingest.normalizer import normalize_label


@dataclass
class FormFieldMatch:
    label: str
    value: str
    start_line: int
    end_line: int


QUESTION_RE = re.compile(r".+\?$")
SECTION_RE = re.compile(
    r"^(section|property details|trustee questions|services connected|rates, taxes and charges|town planning, building approvals and permits)\b",
    re.IGNORECASE,
)


def _is_new_label(line: str, known_labels: set[str]) -> bool:
    clean = line.strip()
    if not clean:
        return False

    normalized = normalize_label(clean)
    if normalized in known_labels:
        return True
    if any(normalized.startswith(label) or label.startswith(normalized) for label in known_labels if label):
        return True

    if QUESTION_RE.match(clean):
        return True

    if SECTION_RE.match(clean):
        return True

    return False


def _is_soft_stop(line: str) -> bool:
    normalized = normalize_label(line)
    return normalized in SOFT_STOP_LABELS


def extract_field_after_label(
    text: str,
    label: str,
    *,
    known_labels: Iterable[str] | None = None,
    max_label_lines: int = 4,
    max_lines: int = 8,
) -> FormFieldMatch | None:
    lines = [line.rstrip() for line in text.splitlines()]
    label_norm = normalize_label(label)
    known_label_set = {normalize_label(item) for item in (known_labels or [])}

    for start_index in range(len(lines)):
        assembled_parts: list[str] = []
        for end_index in range(start_index, min(len(lines), start_index + max_label_lines)):
            assembled_parts.append(lines[end_index].strip())
            assembled = normalize_label(" ".join(part for part in assembled_parts if part))
            if assembled != label_norm:
                continue

            collected: list[str] = []
            start_line = end_index + 2
            end_line = start_line

            for offset, next_line in enumerate(lines[end_index + 1 : end_index + 1 + max_lines], start=1):
                stripped = next_line.strip()
                if not stripped:
                    if collected:
                        break
                    continue

                if _is_soft_stop(stripped):
                    break

                if _is_new_label(stripped, known_label_set):
                    break

                collected.append(stripped)
                end_line = end_index + 1 + offset

            if collected:
                return FormFieldMatch(
                    label=label,
                    value="\n".join(collected).strip(),
                    start_line=start_line,
                    end_line=end_line,
                )

    return None


def extract_question_answer(
    text: str,
    question: str,
    *,
    known_labels: Iterable[str] | None = None,
    max_lines: int = 4,
) -> FormFieldMatch | None:
    return extract_field_after_label(
        text,
        question,
        known_labels=known_labels,
        max_lines=max_lines,
    )
