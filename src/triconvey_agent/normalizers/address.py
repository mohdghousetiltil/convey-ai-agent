from __future__ import annotations

import re

ABBREVIATIONS = {
    "ln": "lane",
    "rd": "road",
    "st": "street",
    "ave": "avenue",
    "dr": "drive",
    "ct": "court",
    "vic": "",
    "australia": "",
}


def normalize_address_for_compare(value: str | None) -> str:
    if not value:
        return ""

    text = value.lower()
    text = re.sub(r"[,]", " ", text)

    for short, full in ABBREVIATIONS.items():
        text = re.sub(rf"\b{re.escape(short)}\b", full, text)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def addresses_equivalent(left: str | None, right: str | None) -> bool:
    return normalize_address_for_compare(left) == normalize_address_for_compare(right)
