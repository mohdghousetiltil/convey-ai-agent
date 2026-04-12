from __future__ import annotations

from collections import defaultdict
from typing import Any

from triconvey_agent.normalizers.address import addresses_equivalent
from triconvey_agent.schemas.documents import DocumentType
from triconvey_agent.schemas.extracted import ConflictItem, FieldValue


def _normalize_compare_value(value: Any) -> Any:
    if isinstance(value, list):
        normalized: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                normalized.append(tuple(sorted((key, str(val).strip().lower()) for key, val in item.items())))
            else:
                normalized.append(str(item).strip().lower())
        return tuple(normalized)
    if isinstance(value, dict):
        return tuple(sorted((key, str(val).strip().lower()) for key, val in value.items()))
    if value is None:
        return None
    return str(value).strip().lower()


def values_equivalent(field_key: str | None, left: Any, right: Any) -> bool:
    if field_key == "property_address" and isinstance(left, str) and isinstance(right, str):
        return addresses_equivalent(left, right)
    return _normalize_compare_value(left) == _normalize_compare_value(right)


def detect_conflicts(
    candidates: list[tuple[DocumentType, str, FieldValue]],
    *,
    field_key: str | None = None,
    chosen_value: Any = None,
) -> list[ConflictItem]:
    seen: dict[Any, list[tuple[DocumentType, str, FieldValue]]] = defaultdict(list)

    for doc_type, source_file, field in candidates:
        seen[_normalize_compare_value(field.value)].append((doc_type, source_file, field))

    if len(seen) <= 1:
        return []

    conflicts: list[ConflictItem] = []
    for grouped in seen.values():
        for doc_type, source_file, field in grouped:
            if chosen_value is not None and values_equivalent(field_key, chosen_value, field.value):
                continue
            conflicts.append(
                ConflictItem(
                    source_file=source_file,
                    source_type=doc_type,
                    value=field.value,
                    reason=f"Conflicting value from {doc_type.value}",
                    evidence=list(field.evidence),
                )
            )
    return conflicts
