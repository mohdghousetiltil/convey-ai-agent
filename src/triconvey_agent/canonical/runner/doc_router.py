from __future__ import annotations

import re
from typing import Literal

DocKind = Literal[
    "land_certificate",
    "water_certificate",
    "owners_corporation",
    "other",
]

WATER_RE = re.compile(
    r"(?:"
    r"water\s+information\s+statement"
    r"|water\s+act\s+1989"
    r"|section\s+158"
    r"|information\s+statement\s+remittance"
    r"|water\s+service\s+fee"
    r"|sewerage\s+service\s+fee"
    r"|yarra\s+valley\s+water"
    r"|south\s+east\s+water"
    r"|greater\s+western\s+water"
    r"|gippsland\s+water"
    r"|east\s+gippsland\s+water"
    r"|barwon\s+water"
    r"|coliban\s+water"
    r"|goulburn\s+valley\s+water"
    r")",
    re.I,
)

OWNER_CORP_RE = re.compile(
    r"(?:"
    r"owners?\s+corporation"
    r"|body\s+corporate"
    r"|strata\s+(?:certificate|report|levy|fees?)"
    r"|oc\s+(?:certificate|report|levy|search)"
    r"|owners?\s+corporation\s+certificate"
    r"|owners?\s+corporation\s+search\s+report"
    r")",
    re.I,
)

LAND_CERT_RE = re.compile(
    r"(?:"
    r"land\s+information\s+certificate"
    r"|rates?\s+charges?\s+and\s+other\s+moneys"
    r"|current\s+year'?s\s+rates"
    r"|current\s+rates\s+and\s+charges"
    r"|current\s+rates\s+levied"
    r"|rates?\s*&\s*charges\s+levied"
    r"|local\s+government\s+act"
    r")",
    re.I,
)


def classify_document_for_rates(doc) -> DocKind:
    filename = getattr(doc, "filename", "") or ""
    raw = getattr(doc, "raw_text", "") or ""
    norm = getattr(doc, "normalized_text", "") or ""

    page_texts: list[str] = []
    for page in (getattr(doc, "pages", []) or [])[:2]:
        page_texts.append(getattr(page, "text", "") or "")
        page_texts.append(getattr(page, "normalized_text", "") or "")

    haystack = "\n".join([filename, raw[:8000], norm[:8000], *page_texts])

    if WATER_RE.search(haystack):
        return "water_certificate"
    if OWNER_CORP_RE.search(haystack):
        return "owners_corporation"
    if LAND_CERT_RE.search(haystack):
        return "land_certificate"
    return "other"
