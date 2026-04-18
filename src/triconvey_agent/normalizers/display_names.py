from __future__ import annotations

COUNCIL_DISPLAY_NAME_MAP = {
    "monash": "City of Monash Council",
    "city of monash": "City of Monash Council",
    "city of monash council": "City of Monash Council",
    "monash city council": "City of Monash Council",
    "indigo": "Indigo Shire Council",
    "indigo shire": "Indigo Shire Council",
    "indigo shire council": "Indigo Shire Council",
    "ballarat": "City of Ballarat",
    "ballaret": "City of Ballarat",
    "city of ballaret": "City of Ballarat",
    "ballarat city council": "City of Ballarat",
    "city of ballarat": "City of Ballarat",
}


def normalize_council_display_name(name: str | None) -> str:
    if not name:
        return "Council"

    cleaned = str(name).strip()
    normalized = COUNCIL_DISPLAY_NAME_MAP.get(cleaned.lower(), cleaned)
    lower = normalized.lower()
    if "council" not in lower and "shire" not in lower and "rural city" not in lower and not lower.startswith("city of "):
        normalized = normalized + " Council"
    return normalized
