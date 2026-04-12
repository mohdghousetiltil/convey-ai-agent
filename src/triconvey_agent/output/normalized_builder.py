from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from triconvey_agent.ingest.pdf_loader import load_pdf_document
from triconvey_agent.normalizers.display_names import normalize_council_display_name
from triconvey_agent.schemas.normalized import (
    AlternativeServices,
    NormalizedAttachment,
    NormalizedOutput,
    NormalizedServices,
)


def _get_nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _title_case_volume_folio(volume_folio: str | None) -> str | None:
    if not volume_folio:
        return None
    value = str(volume_folio).strip()
    value = re.sub(r"\bVOLUME\b", "Volume", value, flags=re.IGNORECASE)
    value = re.sub(r"\bFOLIO\b", "Folio", value, flags=re.IGNORECASE)
    return value


def _date_only(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{2}/\d{2}/\d{4})", str(value))
    return match.group(1) if match else None


def _parse_date(value: str | None) -> datetime | None:
    date_text = _date_only(value)
    if not date_text:
        return None
    try:
        return datetime.strptime(date_text, "%d/%m/%Y")
    except ValueError:
        return None


def _format_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d/%m/%Y")


def _next_day(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value + timedelta(days=1)


def _extract_plan_reference(diagram_location: str | None) -> tuple[str, str] | None:
    """Extract a plan reference from a VIC title diagram_location string.

    Matches patterns like "SEE PS826454D" or "SEE LP123456".
    Victorian plan prefixes:
        PS = Plan of Subdivision (current format)
        LP = Lodged Plan (older format, also subdivision)
        PC = Plan of Consolidation
        TP = Title Plan (legacy, treated as subdivision)
    """
    if not diagram_location:
        return None
    match = re.search(r"\bSEE\s+([A-Z]{2}\d+[A-Z]?)\b", str(diagram_location), flags=re.IGNORECASE)
    if not match:
        return None

    ref = match.group(1).upper()
    if ref.startswith("PC"):
        return ("plan_of_consolidation", ref)
    if ref.startswith("PS") or ref.startswith("LP") or ref.startswith("TP"):
        return ("plan_of_subdivision", ref)
    return None


@lru_cache(maxsize=64)
def _resolve_source_file_path(filename: str) -> Path | None:
    search_roots = [Path.cwd()]
    for root in search_roots:
        for path in root.rglob(filename):
            if path.is_file():
                return path.resolve()
    return None


@lru_cache(maxsize=64)
def _extract_pdf_generated_date(filename: str) -> str | None:
    file_path = _resolve_source_file_path(filename)
    if file_path is None or file_path.suffix.lower() != ".pdf":
        return None

    try:
        document = load_pdf_document(file_path)
    except Exception:
        return None

    text = document.raw_text
    patterns = [
        r"Created at (\d{1,2} [A-Za-z]+ \d{4})",
        r"at (\d{1,2} [A-Za-z]+ \d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            parsed = datetime.strptime(match.group(1), "%d %B %Y")
            return parsed.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return None


def _attachment_exists_by_filename(files: list[str], needle: str) -> bool:
    lowered = needle.lower()
    return any(lowered in file_name.lower() for file_name in files)


def _volume_sort_key(volume_folio: str | None) -> tuple[int, str]:
    if not volume_folio:
        return (999999999, "")
    match = re.search(r"VOLUME\s+(\d+)", volume_folio, flags=re.IGNORECASE)
    if match:
        return (int(match.group(1)), volume_folio)
    return (999999999, volume_folio)


def _title_records(final_output: dict[str, Any]) -> list[dict[str, Any]]:
    records = [
        {
            "source_file": _get_nested(final_output, "source_summary.primary_title_source"),
            "volume_folio": _get_nested(final_output, "vic_title_extract.primary_volume_folio.value"),
            "produced_date": _date_only(_get_nested(final_output, "vic_title_extract.produced_date.value")),
            "diagram_location": _get_nested(final_output, "vic_title_extract.diagram_location.value"),
            "land_description": _get_nested(final_output, "vic_title_extract.land_description.value"),
        }
    ]

    for additional_title in final_output.get("additional_titles", []):
        fields = additional_title.get("fields", {})
        records.append(
            {
                "source_file": additional_title.get("source_file"),
                "volume_folio": _get_nested(fields, "primary_volume_folio.value"),
                "produced_date": _date_only(_get_nested(fields, "produced_date.value")),
                "diagram_location": _get_nested(fields, "diagram_location.value"),
                "land_description": _get_nested(fields, "land_description.value"),
            }
        )

    return sorted(records, key=lambda item: _volume_sort_key(item.get("volume_folio")))


def _build_title_bundle_attachments(final_output: dict[str, Any], files: list[str]) -> list[NormalizedAttachment]:
    attachments: list[NormalizedAttachment] = []

    for record in _title_records(final_output):
        volume_folio = record.get("volume_folio")
        produced_date = record.get("produced_date")
        source_file = record.get("source_file")
        if volume_folio:
            display = f"Register Search Statement {_title_case_volume_folio(volume_folio)}"
            if produced_date:
                display = f"{display} dated {produced_date}"
            attachments.append(
                NormalizedAttachment(
                    doc_type="register_search_statement",
                    display_text=display,
                    exists_as_file=True,
                    source="uploaded_file",
                    source_file=source_file,
                    confidence=0.99,
                )
            )

        plan_reference = _extract_plan_reference(record.get("diagram_location"))
        if not plan_reference:
            continue
        doc_type, ref_number = plan_reference
        exists_as_file = _attachment_exists_by_filename(files, ref_number)
        plan_label = "Plan of Consolidation" if doc_type == "plan_of_consolidation" else "Plan of Subdivision"
        display_text = f"{plan_label} {ref_number}"
        if produced_date:
            display_text = f"{display_text} dated {produced_date}"
        attachments.append(
            NormalizedAttachment(
                doc_type=doc_type,
                display_text=display_text,
                exists_as_file=exists_as_file,
                source="uploaded_file" if exists_as_file else "title_reference_only",
                source_file=source_file if exists_as_file else None,
                confidence=0.8 if exists_as_file else 0.55,
                reference_number=ref_number,
                review_note=None if exists_as_file else "Referenced in title metadata and included under attachment policy.",
            )
        )

    return attachments


def _build_ai_driven_attachments(
    source_documents: list[dict[str, Any]],
) -> list[NormalizedAttachment]:
    """Build the attachment list from AI document summaries.

    Every uploaded PDF that the AI flagged as is_section32_attachment=True
    becomes a confirmed attachment with the date extracted FROM the actual
    document (not a proxy date inferred from the title search date).
    """
    attachments: list[NormalizedAttachment] = []

    for summary in source_documents:
        if not summary.get("is_section32_attachment"):
            continue

        display_name = summary.get("display_name") or summary.get("source_file", "Document")
        doc_date = summary.get("document_date")   # date from actual document
        source_file = summary.get("source_file")
        category = summary.get("document_category", "unknown")

        display_text = display_name
        if doc_date:
            display_text = f"{display_name} dated {doc_date}"

        attachments.append(
            NormalizedAttachment(
                doc_type=category,
                display_text=display_text,
                exists_as_file=True,
                source="uploaded_file",
                source_file=source_file,
                confidence=0.95 if summary.get("date_verified") else 0.80,
                review_note=None if doc_date else "Date not found in document — review required.",
            )
        )

    return attachments


def _build_policy_placeholder_attachments(
    final_output: dict[str, Any],
    source_documents: list[dict[str, Any]],
) -> list[NormalizedAttachment]:
    """Add placeholders ONLY for required documents not already found in uploaded files.

    When AI summaries are present (source_documents non-empty), placeholder dates
    come from the AI-matched document.  When not present, falls back to the title
    search date cycle as before.
    """
    attachments: list[NormalizedAttachment] = []
    council_name = normalize_council_display_name(_get_nested(final_output, "rates_taxes_charges.council.value"))
    water_authority_raw = _get_nested(final_output, "rates_taxes_charges.water_authority.value")
    primary_title_date = _parse_date(_get_nested(final_output, "vic_title_extract.produced_date.value"))
    title_certificate_date = _format_date(_next_day(primary_title_date))
    improved = _get_nested(final_output, "planning_building_permits.improved_past_6_5_years.value")
    new_appliances = _get_nested(final_output, "planning_building_permits.new_appliances_with_gas_electrical_plumbing_work.value")

    # Build a lookup of categories already covered by uploaded files (AI summaries)
    covered: set[str] = {s.get("document_category", "") for s in source_documents if s.get("is_section32_attachment")}

    def _placeholder(doc_type: str, display: str, note: str, conf: float = 0.65) -> NormalizedAttachment:
        return NormalizedAttachment(
            doc_type=doc_type,
            display_text=display,
            exists_as_file=False,
            source="placeholder",
            confidence=conf,
            review_note=note,
        )

    # Council certificates — always needed unless uploaded
    if council_name and "council_building_approval_certificate" not in covered:
        attachments.append(_placeholder(
            "council_building_approval_certificate",
            f"{council_name} Building Approval Certificate dated ##",
            "Office placeholder — obtain from council.",
            0.60,
        ))
    if council_name and "council_rates_certificate" not in covered and "council_land_information_certificate" not in covered:
        attachments.append(_placeholder(
            "council_land_information_certificate",
            f"{council_name} Land Information Certificate dated {title_certificate_date or '##'}",
            "Office placeholder dated from title search cycle.",
            0.75,
        ))

    # Water authority encumbrance — always needed unless uploaded
    water_authority = str(water_authority_raw).strip() if water_authority_raw else None
    if water_authority and "water_encumbrance_certificate" not in covered and "water_information_statement" not in covered:
        water_label = (
            f"{water_authority} Encumbrance Certificate"
            if "water" not in water_authority.lower()
            else f"{water_authority} Water Encumbrance Certificate"
        )
        attachments.append(_placeholder(
            "water_encumbrance_certificate",
            f"{water_label} dated ##",
            "Office placeholder — obtain from water authority.",
            0.70,
        ))

    # Government searches — add placeholders if not uploaded
    if "land_tax_certificate" not in covered:
        attachments.append(_placeholder(
            "land_tax_certificate",
            f"State Revenue Office Land Tax Certificate dated {title_certificate_date or '##'}",
            "Office placeholder dated from title search cycle.",
            0.70,
        ))
    if "vic_roads_certificate" not in covered:
        attachments.append(_placeholder(
            "vic_roads_certificate",
            f"VicRoads Certificate dated {_format_date(primary_title_date) or '##'}",
            "Office placeholder — date comes from actual VicRoads certificate.",
            0.65,
        ))
    if "epa_certificate" not in covered:
        attachments.append(_placeholder(
            "epa_certificate",
            f"EPA Certificate dated {_format_date(primary_title_date) or '##'}",
            "Office placeholder dated from title search cycle.",
            0.65,
        )
    )

    return attachments


def _dedupe_attachments(attachments: list[NormalizedAttachment]) -> list[NormalizedAttachment]:
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[NormalizedAttachment] = []
    for attachment in attachments:
        key = (attachment.doc_type, attachment.display_text, attachment.reference_number)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(attachment)
    return deduped


def _service_connection_status(final_output: dict[str, Any], *paths: str) -> bool | None:
    """Return the connection status for a service by scanning multiple evidence paths.

    Returns:
        False  — confirmed CONNECTED (at least one path has value=True)
        True   — confirmed NOT connected (all found paths have value=False)
        None   — unknown (no evidence found in any path)

    "Any True wins": a single mains path being True overrides any False alternates.
    This prevents a missing key (stored as False) from incorrectly inferring disconnection.
    """
    has_true = False
    has_false = False
    for path in paths:
        value = _get_nested(final_output, path)
        if value is True:
            has_true = True
        elif value is False:
            has_false = True
    if has_true:
        return False  # at least one path confirms connected
    if has_false:
        return True   # all evidence says not present → not connected
    return None       # no evidence either way


def build_normalized_output(final_output: dict[str, Any]) -> NormalizedOutput:
    files = list(_get_nested(final_output, "source_summary.files", []))
    # AI summaries produced by universal_summarizer (empty when --use-ai-extract not used)
    source_documents: list[dict[str, Any]] = final_output.get("source_documents", [])
    review_flags: list[dict[str, str]] = []

    # Derive connection status for each service from vendor form data.
    # Each field returns: False=confirmed connected, True=confirmed not connected, None=unknown.
    # "Any True wins": if ANY mains path is True the service is connected (False returned).
    # Alternative services (bottled_gas, septic_tank, tank_water) are checked separately and
    # kept in AlternativeServices — they don't drive the mains connection flags directly.
    electricity_not_connected = _service_connection_status(
        final_output,
        "services_connected.mains_electricity.value",
    )
    gas_not_connected = _service_connection_status(
        final_output,
        "services_connected.mains_gas.value",
        "services_connected.gas_supply.value",
        "services_connected.gas_connected.value",
    )
    water_not_connected = _service_connection_status(
        final_output,
        "services_connected.mains_water.value",
        "services_connected.water_supply.value",
        "services_connected.water_connected.value",
    )
    sewerage_not_connected = _service_connection_status(
        final_output,
        "services_connected.mains_sewerage.value",
        "services_connected.sewerage.value",
        "services_connected.sewer_connected.value",
    )
    # NBN/VoIP counts as telephone for the Section 32 disclosure.
    telephone_not_connected = _service_connection_status(
        final_output,
        "services_connected.nbn.value",
        "services_connected.telephone.value",
        "services_connected.telephone_services.value",
        "services_connected.telephone_connected.value",
    )

    # If a mains path confirmed connected (False) or not connected (True), use it directly.
    # If unknown (None), fall back to alternative service flags:
    #   bottled_gas=True → mains gas NOT connected (True)
    #   tank_water=True  → mains water NOT connected (True)
    #   septic_tank=True → mains sewerage NOT connected (True)
    def _with_alt_fallback(status: bool | None, alt_path: str) -> bool | None:
        if status is not None:
            return status
        return True if _get_nested(final_output, alt_path) is True else None

    services = NormalizedServices(
        electricity_supply_not_connected=electricity_not_connected,
        gas_supply_not_connected=_with_alt_fallback(
            gas_not_connected, "services_connected.bottled_gas.value"
        ),
        water_supply_not_connected=_with_alt_fallback(
            water_not_connected, "services_connected.tank_water.value"
        ),
        sewerage_not_connected=_with_alt_fallback(
            sewerage_not_connected, "services_connected.septic_tank.value"
        ),
        telephone_not_connected=telephone_not_connected,
        alternative_services=AlternativeServices(
            bottled_gas=_get_nested(final_output, "services_connected.bottled_gas.value"),
            tank_water=_get_nested(final_output, "services_connected.tank_water.value"),
            septic_tank=_get_nested(final_output, "services_connected.septic_tank.value"),
        ),
        reasoning_notes=[
            "Bottled gas is preserved as an alternative service and not treated as mains gas supply.",
            "Tank water is preserved as an alternative service and not treated as mains water supply.",
            "Septic tank is preserved as an alternative service and not treated as sewerage connection.",
        ],
    )

    attachments: list[NormalizedAttachment] = []

    # 1. Title register search statements + plan of subdivision (rules-based, always reliable)
    attachments.extend(_build_title_bundle_attachments(final_output, files))

    # 2. Every uploaded document the AI recognised as a Section 32 attachment
    #    (with dates from the actual document, not proxy dates)
    if source_documents:
        attachments.extend(_build_ai_driven_attachments(source_documents))
    else:
        # Fallback when AI extraction was not used: filename-based confirmed docs
        other_confirmed = _build_other_confirmed_attachments(files)
        attachments.extend(other_confirmed)

    # 3. Placeholders for required documents that were NOT uploaded
    #    (only adds entries not already covered by AI or rules above)
    attachments.extend(_build_policy_placeholder_attachments(final_output, source_documents))

    attachments = _dedupe_attachments(attachments)

    for item in attachments:
        if item.source == "title_reference_only":
            review_flags.append(
                {
                    "type": "attachment_reference_only",
                    "detail": f"{item.doc_type} {item.reference_number} is referenced in title metadata and included by policy.",
                }
            )
        if item.source == "placeholder":
            review_flags.append(
                {
                    "type": "attachment_placeholder",
                    "detail": f"{item.doc_type} is included as an office placeholder and should be reviewed before filing.",
                }
            )

    if _get_nested(final_output, "rates_taxes_charges.owners_corporation.value") is False:
        review_flags.append(
            {
                "type": "owners_corporation_false",
                "detail": "Owners corporation is false; do not auto-mark owners corporation attachments.",
            }
        )

    return NormalizedOutput(
        attachments_normalized=attachments,
        services_normalized=services,
        review_flags=review_flags,
    )


def load_normalized_output(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
