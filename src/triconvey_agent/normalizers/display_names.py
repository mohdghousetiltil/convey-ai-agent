from __future__ import annotations

COUNCIL_DISPLAY_NAME_MAP = {
    "indigo": "Indigo Shire Council",
}


def normalize_council_display_name(name: str | None) -> str:
    if not name:
        return "Council"

    cleaned = str(name).strip()
    normalized = COUNCIL_DISPLAY_NAME_MAP.get(cleaned.lower(), cleaned)
    lower = normalized.lower()
    if "council" not in lower and "shire" not in lower and "rural city" not in lower:
        normalized = normalized + " Council"
    return normalized
