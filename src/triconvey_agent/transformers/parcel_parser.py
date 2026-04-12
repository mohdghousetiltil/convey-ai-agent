from __future__ import annotations

from typing import Any

from triconvey_agent.schemas.extracted import FieldValue


def parse_parcel_details(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, list):
        return field

    lines = [str(item).strip() for item in field.value if str(item).strip()]
    if len(lines) < 3:
        return field

    structured: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        token = lines[index]
        if len(token) == 1 and token.isalpha():
            row: dict[str, Any] = {"parcel_id": token}
            if index + 1 < len(lines):
                row["description"] = lines[index + 1]
            if index + 2 < len(lines):
                row["spi"] = lines[index + 2]
            if index + 3 < len(lines):
                next_token = lines[index + 3]
                if not (len(next_token) == 1 and next_token.isalpha()):
                    row["extra"] = next_token
                    index += 1
            structured.append(row)
            index += 3
        else:
            index += 1

    if structured:
        field.value = structured
    return field


def normalize_parcel_details(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, list):
        return field

    normalized: list[dict[str, Any]] = []
    for row in field.value:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "parcel_id": row.get("parcel_id"),
                "description": row.get("description"),
                "spi": row.get("spi"),
                "parish": row.get("extra"),
            }
        )

    if normalized:
        field.value = normalized
        field.normalized_key = "parcel_details"
    return field
