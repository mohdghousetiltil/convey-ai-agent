from __future__ import annotations

COUNCIL_DISPLAY_NAME_MAP = {
    # Monash
    "monash": "City of Monash Council",
    "city of monash": "City of Monash Council",
    "city of monash council": "City of Monash Council",
    "monash city council": "City of Monash Council",
    # Indigo
    "indigo": "Indigo Shire Council",
    "indigo shire": "Indigo Shire Council",
    "indigo shire council": "Indigo Shire Council",
    # Ballarat
    "ballarat": "Ballarat City Council",
    "ballaret": "Ballarat City Council",
    "city of ballaret": "Ballarat City Council",
    "city of ballarat": "Ballarat City Council",
    "ballarat city council": "Ballarat City Council",
    # Merri-bek
    "merri-bek": "Merri-bek City Council",
    "merribek": "Merri-bek City Council",
    "merri-bek city council": "Merri-bek City Council",
    "merribek city council": "Merri-bek City Council",
    # Yarra
    "yarra": "City of Yarra Council",
    "city of yarra": "City of Yarra Council",
    "city of yarra council": "City of Yarra Council",
    # Boroondara
    "boroondara": "City of Boroondara Council",
    "city of boroondara": "City of Boroondara Council",
    # Stonnington
    "stonnington": "City of Stonnington Council",
    "city of stonnington": "City of Stonnington Council",
    # Glen Eira
    "glen eira": "Glen Eira City Council",
    "glen eira city council": "Glen Eira City Council",
    # Whitehorse
    "whitehorse": "Whitehorse City Council",
    "city of whitehorse": "Whitehorse City Council",
    # Knox
    "knox": "Knox City Council",
    "city of knox": "Knox City Council",
    # Maroondah
    "maroondah": "Maroondah City Council",
    "city of maroondah": "Maroondah City Council",
    # Moonee Valley
    "moonee valley": "Moonee Valley City Council",
    "city of moonee valley": "Moonee Valley City Council",
    # Moreland (now Merri-bek)
    "moreland": "Merri-bek City Council",
    "city of moreland": "Merri-bek City Council",
    # Port Phillip
    "port phillip": "City of Port Phillip Council",
    "city of port phillip": "City of Port Phillip Council",
    # Frankston
    "frankston": "Frankston City Council",
    "city of frankston": "Frankston City Council",
    # Casey
    "casey": "City of Casey Council",
    "city of casey": "City of Casey Council",
    # Greater Dandenong
    "dandenong": "City of Greater Dandenong Council",
    "greater dandenong": "City of Greater Dandenong Council",
    "city of greater dandenong": "City of Greater Dandenong Council",
    # Cardinia
    "cardinia": "Cardinia Shire Council",
    "cardinia shire": "Cardinia Shire Council",
    # Melton
    "melton": "Melton City Council",
    "city of melton": "Melton City Council",
    # Wyndham
    "wyndham": "Wyndham City Council",
    "city of wyndham": "Wyndham City Council",
    # Hobsons Bay
    "hobsons bay": "Hobsons Bay City Council",
    "city of hobsons bay": "Hobsons Bay City Council",
    # Maribyrnong
    "maribyrnong": "Maribyrnong City Council",
    "city of maribyrnong": "Maribyrnong City Council",
    # Brimbank
    "brimbank": "Brimbank City Council",
    "city of brimbank": "Brimbank City Council",
    # Hume
    "hume": "Hume City Council",
    "city of hume": "Hume City Council",
    # Whittlesea
    "whittlesea": "Whittlesea City Council",
    "city of whittlesea": "Whittlesea City Council",
    # Nillumbik
    "nillumbik": "Nillumbik Shire Council",
    "nillumbik shire": "Nillumbik Shire Council",
    # Yarra Ranges
    "yarra ranges": "Yarra Ranges Shire Council",
    "yarra ranges shire": "Yarra Ranges Shire Council",
    # Mornington Peninsula
    "mornington peninsula": "Mornington Peninsula Shire Council",
    "mornington peninsula shire": "Mornington Peninsula Shire Council",
    # Melbourne
    "melbourne": "City of Melbourne Council",
    "city of melbourne": "City of Melbourne Council",
    "city of melbourne council": "City of Melbourne Council",
    # Darebin
    "darebin": "Darebin City Council",
    "city of darebin": "Darebin City Council",
    # Banyule
    "banyule": "Banyule City Council",
    "city of banyule": "Banyule City Council",
    # Manningham
    "manningham": "Manningham City Council",
    "city of manningham": "Manningham City Council",
    # Bayside
    "bayside": "Bayside City Council",
    "city of bayside": "Bayside City Council",
    # Kingston
    "kingston": "Kingston City Council",
    "city of kingston": "Kingston City Council",
}

WATER_AUTHORITY_DISPLAY_NAME_MAP = {
    # Yarra Valley Water
    "yarra valley water": "Yarra Valley Water",
    "yarra valley water corporation": "Yarra Valley Water",
    "yvw": "Yarra Valley Water",
    # South East Water
    "south east water": "South East Water",
    "south east water corporation": "South East Water",
    "sew": "South East Water",
    # City West Water
    "city west water": "City West Water",
    "city west water corporation": "City West Water",
    "cww": "City West Water",
    # Melbourne Water
    "melbourne water": "Melbourne Water",
    "melbourne water corporation": "Melbourne Water",
    # Coliban Water
    "coliban water": "Coliban Water",
    "coliban region water corporation": "Coliban Water",
    # Central Highlands Water
    "central highlands water": "Central Highlands Water",
    "central highlands region water corporation": "Central Highlands Water",
    # Barwon Water
    "barwon water": "Barwon Water",
    "barwon region water corporation": "Barwon Water",
    # Goulburn Valley Water
    "goulburn valley water": "Goulburn Valley Water",
    "goulburn-murray water": "Goulburn-Murray Water",
    # Western Water
    "western water": "Western Water",
    "western region water corporation": "Western Water",
    # East Gippsland Water
    "east gippsland water": "East Gippsland Water",
    # Westernport Water
    "westernport water": "Westernport Water",
    # Lower Murray Water
    "lower murray water": "Lower Murray Water",
    # North East Water
    "north east water": "North East Water",
    # Wannon Water
    "wannon water": "Wannon Water",
}


def normalize_council_display_name(name: str | None) -> str:
    """Return a display-ready council name with 'Council' suffix."""
    if not name:
        return "Council"

    cleaned = str(name).strip()
    normalized = COUNCIL_DISPLAY_NAME_MAP.get(cleaned.lower(), cleaned)
    lower = normalized.lower()
    if (
        "council" not in lower
        and "shire" not in lower
        and "rural city" not in lower
        and not lower.startswith("city of ")
    ):
        normalized = normalized + " Council"
    return normalized


def normalize_water_authority_display_name(name: str | None) -> str:
    """Return a display-ready water authority name."""
    if not name:
        return ""
    cleaned = str(name).strip()
    return WATER_AUTHORITY_DISPLAY_NAME_MAP.get(cleaned.lower(), cleaned)
