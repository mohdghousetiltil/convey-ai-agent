from __future__ import annotations

import re

from triconvey_agent.extractors.common import compact_spaces, make_field
from triconvey_agent.schemas.documents import Document
from triconvey_agent.schemas.extracted import FieldValue

PLAN_PATTERNS = (
    r"Land in (Plan of Consolidation)\s+([A-Z0-9]+)",
    r"Land in (Plan of Subdivision)\s+([A-Z0-9]+)",
    r"Land in (Plan of Survey)\s+([A-Z0-9]+)",
    r"Land in (Title Plan)\s+([A-Z0-9]+)",
)


def _clean_title_text(value: str) -> str:
    value = " ".join(value.split())
    value = value.replace("----------------", " ")
    value = " ".join(value.split())
    value = value.replace("below. DIAGRAM LOCATION", "").strip()
    return value


def _extract_first(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    value = compact_spaces(match.group(1).strip().rstrip("."))
    return _clean_title_text(value)


def _extract_plan_info(text: str) -> tuple[str | None, str | None]:
    for pattern in PLAN_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return compact_spaces(match.group(1)), compact_spaces(match.group(2))
    return None, None


def _extract_registered_proprietor(text: str) -> str | None:
    match = re.search(
        r"REGISTERED PROPRIETOR\s+Estate Fee Simple\s+Sole Proprietor\s+(.+?)\s+[A-Z0-9]+\s+\d{2}/\d{2}/\d{4}",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return compact_spaces(match.group(1))


def _extract_encumbrances(text: str) -> list[dict[str, str]]:
    section_match = re.search(
        r"ENCUMBRANCES, CAVEATS AND NOTICES\s+(.+?)\s+DIAGRAM LOCATION",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not section_match:
        return []

    block = section_match.group(1)
    entries: list[dict[str, str]] = []
    for match in re.finditer(
        r"\b(MORTGAGE|COVENANT|CAVEAT|AGREEMENT|SECTION 173 AGREEMENT)\s+([A-Z0-9]+)\s+(\d{2}/\d{2}/\d{4})",
        block,
        flags=re.IGNORECASE,
    ):
        entries.append(
            {
                "type": compact_spaces(match.group(1)).upper(),
                "number": compact_spaces(match.group(2)),
                "date": compact_spaces(match.group(3)),
            }
        )
    return entries


def extract_vic_title_fields(doc: Document) -> dict[str, FieldValue]:
    text = doc.normalized_text
    result: dict[str, FieldValue] = {}

    field_patterns = {
        "primary_volume_folio": r"(VOLUME\s+\d+\s+FOLIO\s+\d+)",
        "security_no": r"Security no\s*:\s*([A-Z0-9]+)",
        "produced_date": r"Produced\s+([0-9/:\sAPM]+)",
        "land_description": r"LAND DESCRIPTION\s+(.+?)\s+PARENT TITLE",
        "parent_title": r"PARENT TITLE\s+(Volume\s+\d+\s+Folio\s+\d+)",
        "created_by_instrument": r"Created by instrument\s+([A-Z0-9]+\s+\d{2}/\d{2}/\d{4})",
        "diagram_location": r"DIAGRAM LOCATION\s+(.+?)\s+ACTIVITY IN THE LAST 125 DAYS",
        "ect_control": r"eCT Control\s+(.+?)\s+Effective from",
        "ect_effective_from": r"Effective from\s+(\d{2}/\d{2}/\d{4})",
    }

    for key, pattern in field_patterns.items():
        value = _extract_first(text, pattern)
        if value is None:
            continue
        result[key] = make_field(
            value=value,
            document=doc,
            extractor="vic_title_extractor",
            normalized_key=key,
            page=1,
            snippet=value,
            label=key,
            confidence=0.99,
        )

    proprietor = _extract_registered_proprietor(text)
    if proprietor:
        result["registered_proprietor"] = make_field(
            value=proprietor,
            document=doc,
            extractor="vic_title_extractor",
            normalized_key="registered_proprietor",
            page=1,
            snippet=proprietor,
            label="registered proprietor",
            confidence=0.98,
        )

    plan_type, plan_number = _extract_plan_info(text)
    if plan_type:
        result["plan_type"] = make_field(
            value=plan_type,
            document=doc,
            extractor="vic_title_extractor",
            normalized_key="plan_type",
            page=1,
            snippet=f"{plan_type} {plan_number or ''}".strip(),
            label="plan_type",
            confidence=0.98,
        )
    if plan_number:
        result["plan_number"] = make_field(
            value=plan_number,
            document=doc,
            extractor="vic_title_extractor",
            normalized_key="plan_number",
            page=1,
            snippet=f"{plan_type or ''} {plan_number}".strip(),
            label="plan_number",
            confidence=0.98,
        )

    encumbrances = _extract_encumbrances(text)
    if encumbrances:
        result["encumbrances_caveats_notices"] = make_field(
            value=encumbrances,
            document=doc,
            extractor="vic_title_extractor",
            normalized_key="encumbrances_caveats_notices",
            page=1,
            snippet="ENCUMBRANCES, CAVEATS AND NOTICES",
            label="encumbrances, caveats and notices",
            confidence=0.97,
        )

    return result
