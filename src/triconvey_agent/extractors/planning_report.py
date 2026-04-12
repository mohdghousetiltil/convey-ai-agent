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


def _extract_all(text: str, pattern: str, flags: int = re.IGNORECASE | re.MULTILINE) -> list[str]:
    return [compact_spaces(match.group(1)) for match in re.finditer(pattern, text, flags)]


def _extract_utilities(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        if "UTILITIES" not in text:
            continue

        block_match = re.search(
            r"UTILITIES\s+(.+?)\s+(?:View location in VicPlan|Planning Zones|STATE ELECTORATES)",
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
            extractor="planning_report_extractor",
            normalized_key="utilities",
            page=page_number,
            section="UTILITIES",
            snippet="\n".join(lines),
            label="UTILITIES",
            confidence=0.95,
        )
        break

    return result


def _extract_zones(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        zones = _extract_all(text, r"([A-Z ]+ZONE\s+\([A-Z0-9]+\)\s+\([A-Z]+\))")
        if not zones:
            continue

        deduped = list(dict.fromkeys(zones))
        result["planning_zones"] = make_field(
            value=deduped,
            document=doc,
            extractor="planning_report_extractor",
            normalized_key="planning_zones",
            page=page_number,
            section="Planning Zones",
            snippet="\n".join(deduped),
            label="Planning Zones",
            confidence=0.95,
        )
        break

    return result


def _extract_overlays(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        if "Planning Overlays" not in text and "OVERLAY" not in text:
            continue

        affecting = list(dict.fromkeys(_extract_all(text, r"([A-Z ]+OVERLAY\s+\([A-Z0-9]+\)\s+\([A-Z]+\))")))
        if affecting:
            result["planning_overlays_affecting_land"] = make_field(
                value=affecting,
                document=doc,
                extractor="planning_report_extractor",
                normalized_key="planning_overlays_affecting_land",
                page=page_number,
                section="Planning Overlays",
                snippet="\n".join(affecting),
                label="Planning Overlays",
                confidence=0.95,
            )

        nearby_match = re.search(
            r"OTHER OVERLAYS\s+Other overlays in the vicinity not directly affecting this land\s+(.+)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if nearby_match:
            nearby = list(
                dict.fromkeys(_extract_all(nearby_match.group(1), r"([A-Z ]+OVERLAY\s+\([A-Z0-9]+\)\s+\([A-Z]+\))"))
            )
            if nearby:
                result["planning_overlays_nearby"] = make_field(
                    value=nearby,
                    document=doc,
                    extractor="planning_report_extractor",
                    normalized_key="planning_overlays_nearby",
                    page=page_number,
                    section="OTHER OVERLAYS",
                    snippet="\n".join(nearby),
                    label="OTHER OVERLAYS",
                    confidence=0.92,
                )
        break

    return result


def _extract_bushfire(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        if "Designated Bushfire Prone Areas" not in text:
            continue

        is_bpa = "This property is in a designated bushfire prone area" in text
        result["bushfire_prone_area"] = make_field(
            value=is_bpa,
            document=doc,
            extractor="planning_report_extractor",
            normalized_key="bushfire_prone_area",
            page=page_number,
            section="Designated Bushfire Prone Areas",
            snippet="This property is in a designated bushfire prone area." if is_bpa else text[:300],
            label="Designated Bushfire Prone Areas",
            confidence=0.99 if is_bpa else 0.8,
        )
        break

    return result


def _extract_cultural_heritage(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        if "Areas of Aboriginal Cultural Heritage Sensitivity" not in text:
            continue

        is_sensitive = "All or part of this property is an 'area of cultural heritage sensitivity'." in text
        result["aboriginal_cultural_heritage_sensitivity"] = make_field(
            value=is_sensitive,
            document=doc,
            extractor="planning_report_extractor",
            normalized_key="aboriginal_cultural_heritage_sensitivity",
            page=page_number,
            section="Areas of Aboriginal Cultural Heritage Sensitivity",
            snippet="All or part of this property is an 'area of cultural heritage sensitivity'.",
            label="Areas of Aboriginal Cultural Heritage Sensitivity",
            confidence=0.97,
        )
        break

    return result


def extract_planning_report_fields(doc: Document) -> dict[str, FieldValue]:
    text = doc.normalized_text
    result: dict[str, FieldValue] = {}

    single_fields = [
        ("planning_scheme", r"Planning Scheme:\s+(Planning Scheme\s*-\s*[^\n]+)"),
        ("fire_authority", r"Fire Authority:\s+([^\n]+)"),
        ("registered_aboriginal_party", r"Registered Aboriginal Party:\s+([^\n]+)"),
        ("local_government_area", r"PROPERTY DETAILS\s+[^\n]+\s+[^\n]+\s+[^\n]+\s+([A-Z][A-Za-z]+)"),
    ]

    for key, pattern in single_fields:
        value = _first_match(text, pattern, flags=re.IGNORECASE | re.DOTALL)
        if not value:
            continue
        result[key] = make_field(
            value=value,
            document=doc,
            extractor="planning_report_extractor",
            normalized_key=key,
            page=1,
            snippet=value,
            label=key,
            confidence=0.92,
        )

    result.update(_extract_utilities(doc))
    result.update(_extract_zones(doc))
    result.update(_extract_overlays(doc))
    result.update(_extract_bushfire(doc))
    result.update(_extract_cultural_heritage(doc))

    return result
