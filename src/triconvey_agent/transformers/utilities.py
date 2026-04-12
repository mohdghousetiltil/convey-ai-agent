from __future__ import annotations

from triconvey_agent.schemas.extracted import FieldValue


def normalize_utilities(field: FieldValue | None) -> FieldValue | None:
    if field is None or not isinstance(field.value, list):
        return field

    values = [str(item).strip() for item in field.value if str(item).strip()]
    if not values:
        return field

    field.value = {
        "water_authorities": [item for item in values if "water" in item.lower()],
        "power_provider": next((item for item in values if "ausnet" in item.lower()), None),
        "drainage": next((item for item in values if "drainage" in item.lower()), None),
        "raw": values,
    }
    return field
