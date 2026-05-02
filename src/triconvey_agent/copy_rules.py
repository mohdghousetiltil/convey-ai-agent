from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable


_GENERIC_AUTHORITY_SUFFIXES = {
    "authority",
    "authorities",
    "corp",
    "corporation",
    "limited",
    "ltd",
}

_WORD_FIXES = {
    "vally": "valley",
}


@dataclass(frozen=True)
class CopyRuleMatch:
    authority_name: str
    annual_amount: float
    score: float
    matched_on: str


def normalize_authority_name(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""

    raw = raw.replace("&", " and ")
    tokens = [segment for segment in re.split(r"[^a-z0-9]+", raw) if segment]
    cleaned: list[str] = []
    for token in tokens:
        fixed = _WORD_FIXES.get(token, token)
        if fixed in _GENERIC_AUTHORITY_SUFFIXES:
            continue
        cleaned.append(fixed)
    return " ".join(cleaned)


def _token_set(value: str) -> set[str]:
    return {token for token in value.split() if token}


def score_authority_match(left: str | None, right: str | None) -> float:
    normalized_left = normalize_authority_name(left)
    normalized_right = normalize_authority_name(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0

    left_tokens = _token_set(normalized_left)
    right_tokens = _token_set(normalized_right)
    if not left_tokens or not right_tokens:
        return 0.0

    overlap = len(left_tokens & right_tokens)
    token_score = overlap / max(len(left_tokens), len(right_tokens))
    sequence_score = SequenceMatcher(None, normalized_left, normalized_right).ratio()

    if left_tokens <= right_tokens or right_tokens <= left_tokens:
        token_score = max(token_score, 0.96)

    return max(token_score, sequence_score)


def find_best_copy_rule_match(
    authority_name: str | None,
    rules: Iterable[tuple[str, float]],
    *,
    minimum_score: float = 0.86,
) -> CopyRuleMatch | None:
    normalized_authority = normalize_authority_name(authority_name)
    if not normalized_authority:
        return None

    best: CopyRuleMatch | None = None
    for rule_name, annual_amount in rules:
        normalized_rule = normalize_authority_name(rule_name)
        if not normalized_rule:
            continue
        score = score_authority_match(normalized_authority, normalized_rule)
        if score < minimum_score:
            continue
        matched_on = "exact_normalized" if score >= 0.999 else "fuzzy_normalized"
        candidate = CopyRuleMatch(
            authority_name=rule_name,
            annual_amount=float(annual_amount),
            score=score,
            matched_on=matched_on,
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best
