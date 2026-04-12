from __future__ import annotations

import re

from triconvey_agent.extractors.common import compact_spaces, iter_page_text, make_field
from triconvey_agent.schemas.documents import Document
from triconvey_agent.schemas.extracted import FieldValue


def _first_match(text: str, pattern: str, flags: int = re.IGNORECASE | re.MULTILINE) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return compact_spaces(match.group(1))


def _extract_addresses(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        if "ADDRESS DETAILS" not in text:
            continue

        block_match = re.search(
            r"ADDRESS DETAILS\s+These addresses have been found for this property\s+Address\s+(.+?)\s+PARCEL DETAILS",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not block_match:
            continue

        lines = [line.strip() for line in block_match.group(1).splitlines() if line.strip()]
        if not lines:
            continue

        result["address_candidates"] = make_field(
            value=lines,
            document=doc,
            extractor="property_report_extractor",
            normalized_key="address_candidates",
            page=page_number,
            section="ADDRESS DETAILS",
            snippet="\n".join(lines),
            label="ADDRESS DETAILS",
            confidence=0.95,
        )
        break

    return result


def _extract_parcel_details(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        if "PARCEL DETAILS" not in text:
            continue

        block_match = re.search(
            r"PARCEL DETAILS\s+The letter in the first column identifies the parcel in the diagram above\s+(.+?)\s+UTILITIES",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not block_match:
            continue

        lines = [line.strip() for line in block_match.group(1).splitlines() if line.strip()]
        if not lines:
            continue

        result["parcel_details_raw"] = make_field(
            value=lines,
            document=doc,
            extractor="property_report_extractor",
            normalized_key="parcel_details_raw",
            page=page_number,
            section="PARCEL DETAILS",
            snippet="\n".join(lines),
            label="PARCEL DETAILS",
            confidence=0.9,
        )
        break

    return result


def _extract_utilities(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        if "UTILITIES" not in text:
            continue

        block_match = re.search(
            r"UTILITIES\s+(.+?)\s+STATE ELECTORATES",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not block_match:
            continue

        lines = [line.strip() for line in block_match.group(1).splitlines() if line.strip()]
        if not lines:
            continue

        result["utilities"] = make_field(
            value=lines,
            document=doc,
            extractor="property_report_extractor",
            normalized_key="utilities",
            page=page_number,
            section="UTILITIES",
            snippet="\n".join(lines),
            label="UTILITIES",
            confidence=0.95,
        )
        break

    return result


def extract_property_report_fields(doc: Document) -> dict[str, FieldValue]:
    text = doc.normalized_text
    result: dict[str, FieldValue] = {}

    simple_patterns = [
        ("property_address", r"PROPERTY DETAILS\s+([^\n]+)"),
        ("parcel_count_text", r"This property has\s+([^\n]+)"),
        ("area_sqm", r"Area:\s*([0-9,]+)\s*sq\.?\s*m"),
        ("area_ha", r"\(([0-9.]+)\s*ha\)"),
        ("perimeter_m", r"Perimeter:\s*([0-9,]+)\s*m"),
        ("council_property_number", r"PROPERTY DETAILS\s+[^\n]+\s+[^\n]+\s+[^\n]+\s+[A-Z]+\s+([0-9]{2,})"),
        ("legislative_council", r"Legislative Council:\s*([^\n]+)"),
        ("legislative_assembly", r"Legislative Assembly:\s*([^\n]+)"),
    ]

    for key, pattern in simple_patterns:
        value = _first_match(text, pattern, flags=re.IGNORECASE | re.DOTALL)
        if not value:
            continue
        result[key] = make_field(
            value=value,
            document=doc,
            extractor="property_report_extractor",
            normalized_key=key,
            page=1,
            snippet=value,
            label=key,
            confidence=0.91,
        )

    result.update(_extract_addresses(doc))
    result.update(_extract_parcel_details(doc))
    result.update(_extract_utilities(doc))

    return result
