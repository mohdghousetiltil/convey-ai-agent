from __future__ import annotations

import re

from triconvey_agent.schemas.extracted import FieldValue

TITLE_REF_PATTERN = re.compile(
    r"(V\d+\s*F\d+|CA\s*\d+[A-Z]?\s*Sec\s*[A-Z])",
    flags=re.IGNORECASE,
)


def parse_title_references(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, str):
        return field

    matches = [match.group(1).strip() for match in TITLE_REF_PATTERN.finditer(field.value)]
    if matches:
        field.value = matches
    return field
