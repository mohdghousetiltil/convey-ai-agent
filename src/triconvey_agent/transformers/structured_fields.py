from __future__ import annotations

import re

from triconvey_agent.schemas.extracted import FieldValue


def split_beneficiaries(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, str):
        return field

    raw = field.value.strip()
    if not raw:
        return field

    parts = re.split(r"\s+and\s+|,", raw)
    cleaned = [part.strip() for part in parts if part.strip()]
    if not cleaned:
        return field

    field.value = cleaned
    return field


def normalize_money_string(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, str):
        return field

    value = field.value.strip()
    if not value:
        return field

    field.value = value
    return field


def cleanup_zone_list(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, list):
        return field

    cleaned: list[str] = []
    for item in field.value:
        text = str(item).strip()
        if text.upper().startswith("SCHEDULE TO THE "):
            continue
        cleaned.append(text)

    field.value = cleaned
    return field
