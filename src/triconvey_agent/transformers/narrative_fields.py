from __future__ import annotations

import re
from typing import Any

from triconvey_agent.schemas.extracted import FieldValue

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
MONEY_RE = re.compile(r"(?:\$\s?[\d,]+(?:\.\d{2})?|[\d,]+\.\d{2})")
APPLIANCE_HINTS = [
    "oven",
    "cooktop",
    "rangehood",
    "dishwasher",
    "cupboards",
    "pantry",
    "kitchen",
    "butlers pantry",
    "appliances",
]


def parse_new_appliance_details(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, str):
        return field

    text = field.value.strip()
    if not text:
        return field

    year_match = YEAR_RE.search(text)
    money_match = MONEY_RE.search(text)

    structured: dict[str, Any] = {
        "raw_text": text,
        "item": None,
        "installed_year": year_match.group(0) if year_match else None,
        "cost": money_match.group(0) if money_match else None,
    }

    cleaned = text
    if structured["installed_year"]:
        cleaned = cleaned.replace(str(structured["installed_year"]), "").strip(" ,.-")
    if structured["cost"]:
        cleaned = cleaned.replace(str(structured["cost"]), "").strip(" ,.-")

    structured["item"] = cleaned or None
    field.value = structured
    return field


def parse_improvement_details(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, str):
        return field

    text = field.value.strip()
    if not text:
        return field

    structured: dict[str, Any] = {
        "raw_text": text,
        "described_work": text,
        "cost": None,
        "completion_date": None,
    }

    money_match = MONEY_RE.search(text)
    year_match = YEAR_RE.search(text)

    if money_match:
        structured["cost"] = money_match.group(0)
    if year_match:
        structured["completion_date"] = year_match.group(0)

    field.value = structured
    return field


def enrich_improvement_details(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, dict):
        return field

    raw_text = field.value.get("raw_text") or field.value.get("described_work")
    if not isinstance(raw_text, str):
        return field

    text = raw_text.lower()
    tags = sorted({hint for hint in APPLIANCE_HINTS if hint in text})

    field.value = {
        **field.value,
        "keywords": tags,
        "likely_category": "kitchen_improvement" if "kitchen" in text or "butlers pantry" in text else None,
    }
    return field


def mark_incomplete_appliance_details(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, dict):
        return field

    item = field.value.get("item")
    year = field.value.get("installed_year")
    cost = field.value.get("cost")

    field.value = {
        **field.value,
        "is_incomplete": item is None and year is not None and cost is None,
    }
    return field
