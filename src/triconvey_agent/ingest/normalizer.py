from __future__ import annotations

import re
import unicodedata
from typing import Any


DASH_LIKE_VALUES = {"", "-", "--", "---", "na", "nil", "none", "notapplicable"}


def clean_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("Sectopm", "Section")
    return clean_whitespace(text)


def normalize_label(label: str | None) -> str:
    if not label:
        return ""
    label = normalize_text(label).lower()
    label = re.sub(r"[^a-z0-9\s]", " ", label)
    label = re.sub(r"\s+", " ", label).strip()
    return label


def normalize_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = normalize_text(value)
        normalized = normalize_label(cleaned)
        collapsed = re.sub(r"[^a-z0-9]", "", normalized)
        if normalized in DASH_LIKE_VALUES or collapsed in DASH_LIKE_VALUES:
            return None
        return cleaned
    return value


def normalize_boolean_text(value: str | None) -> bool | None:
    if not value:
        return None
    normalized = normalize_label(value)
    if normalized.startswith("yes"):
        return True
    if normalized.startswith("no"):
        return False
    return None


def filename_key(name: str) -> str:
    stem = name.rsplit(".", maxsplit=1)[0]
    return normalize_label(stem)


def to_search_blob(*parts: str | None) -> str:
    normalized_parts = [normalize_label(part) for part in parts if part]
    return " ".join(part for part in normalized_parts if part)


def contains_all_terms(haystack: str, terms: tuple[str, ...]) -> bool:
    return all(term in haystack for term in terms)


def collapse_snippet(text: str, max_len: int = 220) -> str:
    text = clean_whitespace(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."
