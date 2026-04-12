from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from triconvey_agent.output.normalized_builder import build_normalized_output
from triconvey_agent.normalizers.display_names import normalize_council_display_name

FAILURE_TO_COMPLY_TEXT = (
    "To the best of the Vendor's knowledge there is no existing failure to comply "
    "with the terms of any easements, covenants or other similar restriction."
)

DUE_DILIGENCE_TEXT = "Is attached"

# Fields the client always wants set to a specific value, regardless of what
# the extracted data says.  Applied last in map_answers() so they always win.
# Pass a custom dict (or {}) to map_answers(client_overrides=...) to change
# these per-client without touching code.
CLIENT_POLICY_OVERRIDES: dict[str, Any] = {
    "s32_1.rates_their_amounts_are": True,
    "s32_1.rates_total_does_not_exceed_check": True,
    "s32_1.rates_in_attached_certificates": True,
    "s32_1.no_amounts_beyond_listed": True,
    "s32_2.easements_in_title_documents": True,
    "s32_2.easement_failure_to_comply_check": True,
    "s32_2.easement_failure_to_comply_text": FAILURE_TO_COMPLY_TEXT,
    "s32_2.planning_certificate_attached": True,
}

def load_client_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load client policy overrides from a JSON file.

    Looks for client_policy.json in the current working directory by default.
    Falls back to CLIENT_POLICY_OVERRIDES if the file doesn't exist.
    Null values in the file are skipped (let data decide for that field).
    """
    policy_path = Path(path) if path else Path.cwd() / "client_policy.json"
    if not policy_path.exists():
        return dict(CLIENT_POLICY_OVERRIDES)

    raw = json.loads(policy_path.read_text(encoding="utf-8"))
    # Strip comments and null values — null means "let data decide"
    return {k: v for k, v in raw.items() if not k.startswith("_comment") and v is not None}


ATTACHMENT_POLICY_ORDER = [
    "council_building_approval_certificate",
    "council_land_information_certificate",
    "water_encumbrance_certificate",
    "land_tax_certificate",
    "detailed_property_report",
    "owner_builder_report",
    "domestic_building_insurance",
    "vic_roads_certificate",
    "epa_certificate",
]


def normalize_text(text: str | None) -> str:
    if not text:
        return ""

    normalized = text.lower().strip()
    normalized = re.sub(r"\b\d+(\.\d+)?\b", " ", normalized)
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def get_nested_value(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def parse_currency_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text in {"-", "–", "n/a", "N/A"}:
        return None

    text = text.replace("$", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def format_currency(value: float | None) -> str | None:
    if value is None:
        return None
    return f"${value:,.2f}"


def safe_total_with_buffer(amounts: list[Any], buffer_amount: float = 1000) -> float | None:
    total = 0.0
    found_any = False

    for amount in amounts:
        parsed = parse_currency_to_float(amount)
        if parsed is not None:
            total += parsed
            found_any = True

    if not found_any:
        return None

    return round(total + buffer_amount, 2)


def bool_from_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return any(item not in (None, "", [], {}) for item in value.values())
    return None


def build_yes_no_indexes(final_output: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    yes_questions = final_output.get("section32_questions", {}).get("yes_questions", {})
    no_questions = final_output.get("section32_questions", {}).get("no_questions", {})

    yes_norm = {normalize_text(question): payload for question, payload in yes_questions.items()}
    no_norm = {normalize_text(question): payload for question, payload in no_questions.items()}
    return yes_norm, no_norm


def get_question_lookup(tab_json: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for tab in tab_json.get("tabs", []):
        for question in tab.get("questions", []):
            lookup[str(question["question_id"])] = question
    return lookup


def set_answer(question_lookup: dict[str, dict[str, Any]], question_id: str, value: Any) -> None:
    if question_id in question_lookup:
        question_lookup[question_id]["answer"] = value


def map_inverted_service(is_not_connected: bool | None) -> bool | None:
    """Map a service connection status to a TriConvey "tick if NOT connected" checkbox value.

    Args:
        is_not_connected:
            True  — confirmed NOT connected → tick the box (True)
            False — confirmed connected     → explicitly untick (False)
            None  — unknown                 → leave unanswered (None)
    """
    return is_not_connected  # direct passthrough: True/False/None all valid


def _apply_scope(
    output: dict[str, Any],
    scope: frozenset[str],
) -> None:
    """Clear answers for questions outside scope and mark them out_of_scope.

    Called after all data-driven mapping so we don't waste work, and before
    policy overrides so policy can still force values regardless of scope.
    Questions tagged out_of_scope are excluded from the AI confirmation pass.
    """
    for tab in output.get("tabs", []):
        for question in tab.get("questions", []):
            if question["question_id"] not in scope:
                question["answer"] = None
                question["skip_reason"] = "out_of_scope"


def _apply_client_policy_overrides(
    question_lookup: dict[str, dict[str, Any]],
    overrides: dict[str, Any] | None = None,
) -> None:
    """Apply client policy overrides last so they always win over data-driven answers.

    Pass overrides={} to disable all policy defaults for a specific client.
    Pass overrides={"s32_1.rates_their_amounts_are": False} to selectively
    change individual fields without touching the defaults.
    """
    active = overrides if overrides is not None else CLIENT_POLICY_OVERRIDES
    for question_id, value in active.items():
        set_answer(question_lookup, question_id, value)


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output


def title_case_volume_folio(volume_folio: str | None) -> str | None:
    if not volume_folio:
        return None
    value = str(volume_folio).strip()
    value = re.sub(r"\bVOLUME\b", "Volume", value, flags=re.IGNORECASE)
    value = re.sub(r"\bFOLIO\b", "Folio", value, flags=re.IGNORECASE)
    return value


def date_only(value: str | None) -> str:
    if not value:
        return "##"
    match = re.search(r"(\d{2}/\d{2}/\d{4})", str(value))
    return match.group(1) if match else "##"


def extract_plan_no_from_land_desc(land_desc: str | None) -> str | None:
    if not land_desc:
        return None
    match = re.search(r"plan of consolidation\s+([A-Z0-9]+)", land_desc, flags=re.IGNORECASE)
    if not match:
        return None

    plan_no = match.group(1).upper()
    if not plan_no.startswith("PC"):
        plan_no = f"PC{plan_no}"
    return plan_no


def extract_plan_no_from_diagram(diagram_location: str | None) -> str | None:
    if not diagram_location:
        return None
    match = re.search(r"SEE\s+([A-Z0-9]+)", str(diagram_location), flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def extract_plan_reference(land_desc: str | None, diagram_location: str | None) -> tuple[str, str] | None:
    plan_of_consolidation = extract_plan_no_from_land_desc(land_desc)
    if plan_of_consolidation:
        return ("Plan of Consolidation", plan_of_consolidation)

    diagram_plan = extract_plan_no_from_diagram(diagram_location)
    if not diagram_plan:
        return None

    if diagram_plan.startswith("PC"):
        return ("Plan of Consolidation", diagram_plan)
    if diagram_plan.startswith("TP"):
        return ("Plan of Subdivision", diagram_plan)
    return None


def first_water_authority_name(final_output: dict[str, Any]) -> str | None:
    water_authorities = get_nested_value(final_output, "services_connected.utilities.value.water_authorities", [])
    if water_authorities:
        return str(water_authorities[0])
    return None


def collect_mortgage_numbers(final_output: dict[str, Any]) -> list[str]:
    found: list[str] = []

    primary_entries = get_nested_value(final_output, "vic_title_extract.encumbrances_caveats_notices.value", [])
    for row in primary_entries:
        if str(row.get("type", "")).upper() == "MORTGAGE" and row.get("number"):
            found.append(str(row["number"]))

    for additional_title in final_output.get("additional_titles", []):
        entries = get_nested_value(additional_title, "fields.encumbrances_caveats_notices.value", [])
        for row in entries:
            if str(row.get("type", "")).upper() == "MORTGAGE" and row.get("number"):
                found.append(str(row["number"]))

    return dedupe_keep_order(found)


def needs_right_to_sell_docs(final_output: dict[str, Any]) -> bool:
    _ = final_output
    return False


def get_vendor_section27(final_output: dict[str, Any]) -> bool:
    _ = final_output
    return False


def get_authority_rows(final_output: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    council_name = get_nested_value(final_output, "rates_taxes_charges.council.value")
    council_rates = get_nested_value(final_output, "rates_taxes_charges.council_rates.value")
    if council_name and parse_currency_to_float(council_rates) is not None:
        rows.append(
            {
                "authority": normalize_council_display_name(str(council_name)),
                "amount": council_rates,
                "interest": None,
            }
        )

    water_authority = get_nested_value(final_output, "rates_taxes_charges.water_authority.value")
    water_rates = get_nested_value(final_output, "rates_taxes_charges.water_rates.value")
    if water_authority and parse_currency_to_float(water_rates) is not None:
        rows.append(
            {
                "authority": str(water_authority),
                "amount": water_rates,
                "interest": None,
            }
        )

    land_tax_payable = get_nested_value(final_output, "rates_taxes_charges.land_tax_payable.value")
    land_tax_amount = get_nested_value(final_output, "rates_taxes_charges.land_tax_amount.value")
    if land_tax_payable is True:
        rows.append(
            {
                "authority": "State Revenue Office, Land Tax - Annually",
                "amount": land_tax_amount,
                "interest": None,
            }
        )

    return rows


def choose_planning_overlays(final_output: dict[str, Any]) -> str | None:
    overlays = get_nested_value(final_output, "planning_building_permits.planning_overlays_affecting_land.value", [])
    if not overlays:
        return None
    return "\n".join(str(item) for item in overlays)


def choose_planning_zone(final_output: dict[str, Any]) -> str | None:
    zones = get_nested_value(final_output, "planning_building_permits.planning_zones.value", [])
    if not zones:
        return None

    first_zone = str(zones[0]).upper()
    if "FARMING ZONE" in first_zone or "(FZ)" in first_zone:
        return "FZ - Farming Zone"
    if "PCRZ" in first_zone:
        return "PCRZ - Public Conservation and Resource Zone"
    return str(zones[0])


def build_attachments_list(normalized_output: dict[str, Any], final_output: dict[str, Any]) -> str:
    items: list[str] = ["- Due Diligence Checklist"]
    attachments = normalized_output.get("attachments_normalized", [])
    excluded_doc_types = {"planning_property_report"}
    title_bundle_doc_types = {
        "register_search_statement",
        "plan_of_subdivision",
        "plan_of_consolidation",
    }
    allowed_doc_types = set(ATTACHMENT_POLICY_ORDER) | title_bundle_doc_types
    allowed_sources = {"uploaded_file", "title_reference_only", "placeholder"}

    title_bundle_attachments: list[dict[str, Any]] = []
    for attachment in attachments:
        if attachment.get("doc_type") not in title_bundle_doc_types:
            continue
        if attachment.get("doc_type") in excluded_doc_types:
            continue
        if attachment.get("source") not in allowed_sources:
            continue
        display_text = attachment.get("display_text")
        if display_text:
            title_bundle_attachments.append(attachment)

    policy_attachments: list[dict[str, Any]] = []
    for doc_type in ATTACHMENT_POLICY_ORDER:
        for attachment in attachments:
            if attachment.get("doc_type") != doc_type:
                continue
            if attachment.get("doc_type") in excluded_doc_types:
                continue
            if attachment.get("source") not in allowed_sources:
                continue
            display_text = attachment.get("display_text")
            if not display_text:
                continue
            policy_attachments.append(attachment)

    ordered_attachments = [*title_bundle_attachments, *policy_attachments]
    for attachment in attachments:
        doc_type = attachment.get("doc_type")
        if doc_type in allowed_doc_types:
            continue
        if doc_type in excluded_doc_types:
            continue
        if attachment.get("source") not in allowed_sources:
            continue
        display_text = attachment.get("display_text")
        if display_text:
            ordered_attachments.append(attachment)

    for attachment in ordered_attachments:
        items.append(f"- {attachment['display_text']}")

    return "\n".join(dedupe_keep_order(items))


def _apply_yes_no_fallback(final_output: dict[str, Any], question_lookup: dict[str, dict[str, Any]]) -> None:
    yes_norm, no_norm = build_yes_no_indexes(final_output)

    aliases = {
        "s32_2.bushfire_prone_area": "is the land in a bushfire prone area",
        "s32_4.gaic_applies": "growth area infrastructure contribution applies",
        "s32_1.land_tax_reform_scheme_land": "do you pay land tax",
        "s32_3.building_permits_in_attached_cert": "have any building permits been issued in the last 7 years",
    }

    for question_id, question in question_lookup.items():
        if question.get("answer") is not None:
            continue

        label_norm = normalize_text(str(question.get("label", "")))
        alias_norm = aliases.get(question_id)

        if label_norm in yes_norm or (alias_norm and alias_norm in yes_norm):
            question["answer"] = True
            continue

        if label_norm in no_norm or (alias_norm and alias_norm in no_norm):
            question["answer"] = False


def map_sec32_1(final_output: dict[str, Any], question_lookup: dict[str, dict[str, Any]]) -> None:
    rows = get_authority_rows(final_output)

    total_cap = safe_total_with_buffer([row["amount"] for row in rows], buffer_amount=1000)
    set_answer(question_lookup, "s32_1.rates_total_does_not_exceed_amount", format_currency(total_cap))

    if len(rows) > 0:
        set_answer(question_lookup, "s32_1.outgoing_1_authority", rows[0]["authority"])
        set_answer(question_lookup, "s32_1.outgoing_1_amount", rows[0]["amount"])
        set_answer(question_lookup, "s32_1.outgoing_1_interest", rows[0]["interest"])

    if len(rows) > 1:
        set_answer(question_lookup, "s32_1.outgoing_2_authority", rows[1]["authority"])
        set_answer(question_lookup, "s32_1.outgoing_2_amount", rows[1]["amount"])

    set_answer(question_lookup, "s32_1.sale_subject_to_mortgage", bool(collect_mortgage_numbers(final_output)))


def map_sec32_2(final_output: dict[str, Any], question_lookup: dict[str, dict[str, Any]]) -> None:
    road_access = get_nested_value(final_output, "planning_building_permits.road_access.value")
    if road_access is True:
        set_answer(question_lookup, "s32_2.road_access_none", False)
    elif road_access is False:
        set_answer(question_lookup, "s32_2.road_access_none", True)

    set_answer(
        question_lookup,
        "s32_2.bushfire_prone_area",
        get_nested_value(final_output, "planning_building_permits.bushfire_prone_area.value"),
    )
    set_answer(
        question_lookup,
        "s32_2.planning_scheme_name",
        get_nested_value(final_output, "planning_building_permits.planning_scheme.value"),
    )
    set_answer(
        question_lookup,
        "s32_2.planning_responsible_authority",
        normalize_council_display_name(get_nested_value(final_output, "rates_taxes_charges.council.value")),
    )
    set_answer(question_lookup, "s32_2.planning_zone", choose_planning_zone(final_output))
    set_answer(question_lookup, "s32_2.planning_overlay_name", choose_planning_overlays(final_output))


def map_sec32_3(final_output: dict[str, Any], question_lookup: dict[str, dict[str, Any]]) -> None:
    _ = final_output
    _ = question_lookup


def map_sec32_4(
    final_output: dict[str, Any], question_lookup: dict[str, dict[str, Any]], normalized_output: dict[str, Any]
) -> None:
    services = normalized_output.get("services_normalized", {})
    service_question_map = {
        "s32_4.service_electricity_not_connected": services.get("electricity_supply_not_connected"),
        "s32_4.service_gas_not_connected": services.get("gas_supply_not_connected"),
        "s32_4.service_water_not_connected": services.get("water_supply_not_connected"),
        "s32_4.service_sewerage_not_connected": services.get("sewerage_not_connected"),
        "s32_4.service_telephone_not_connected": services.get("telephone_not_connected"),
    }
    for question_id, value in service_question_map.items():
        set_answer(question_lookup, question_id, map_inverted_service(value))

    gaic = get_nested_value(final_output, "planning_building_permits.gaic_trigger.value")
    set_answer(question_lookup, "s32_4.gaic_applies", gaic)


def map_sec32_5(final_output: dict[str, Any], question_lookup: dict[str, dict[str, Any]]) -> None:
    _ = final_output
    _ = question_lookup


def map_sec32_6(
    final_output: dict[str, Any], question_lookup: dict[str, dict[str, Any]], normalized_output: dict[str, Any]
) -> None:
    set_answer(question_lookup, "s32_6.due_diligence_checklist", DUE_DILIGENCE_TEXT)
    set_answer(question_lookup, "s32_6.attachments_list", build_attachments_list(normalized_output, final_output))


def build_report(answered_template: dict[str, Any], normalized_output: dict[str, Any]) -> dict[str, Any]:
    mapped_questions: list[dict[str, Any]] = []
    unmapped_questions: list[dict[str, Any]] = []
    out_of_scope_questions: list[dict[str, Any]] = []
    attachment_review_items: list[dict[str, Any]] = []

    for tab in answered_template.get("tabs", []):
        tab_name = tab.get("tab_name")
        for question in tab.get("questions", []):
            item = {
                "tab_name": tab_name,
                "question_id": question["question_id"],
                "label": question["label"],
                "answer": question.get("answer"),
            }

            if question.get("skip_reason") == "out_of_scope":
                out_of_scope_questions.append(item)
                continue

            answer = question.get("answer")
            if answer is None:
                unmapped_questions.append(item)
                continue

            mapped_questions.append(item)

            if isinstance(answer, str) and "dated ##" in answer:
                attachment_review_items.append(
                    {
                        "question_id": question["question_id"],
                        "label": question["label"],
                        "reason": "Contains placeholder attachment dates that need human review.",
                    }
                )

    return {
        "mapped_count": len(mapped_questions),
        "unmapped_count": len(unmapped_questions),
        "out_of_scope_count": len(out_of_scope_questions),
        "mapped_questions": mapped_questions,
        "unmapped_questions": unmapped_questions,
        "out_of_scope_questions": out_of_scope_questions,
        "attachment_review_items": attachment_review_items,
        "normalization_review_flags": normalized_output.get("review_flags", []),
    }


def map_answers(
    final_output: dict[str, Any],
    triconvey_template: dict[str, Any],
    normalized_output: dict[str, Any] | None = None,
    client_overrides: dict[str, Any] | None = None,
    scope: frozenset[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map final_output onto the triconvey template.

    client_overrides: pass a dict to replace CLIENT_POLICY_OVERRIDES for this
    client.  Pass {} to disable all policy defaults.  Pass None (default) to
    use the standard CLIENT_POLICY_OVERRIDES.

    scope: frozenset of question_ids that should be filled for this matter.
    Questions outside scope are cleared and tagged out_of_scope in the report.
    Build from output.scope.build_scope(final_output).  Pass None to fill all.
    """
    output = deepcopy(triconvey_template)
    question_lookup = get_question_lookup(output)
    normalized = normalized_output or build_normalized_output(final_output).model_dump(mode="json")

    # Step 1 — data-driven fill
    map_sec32_1(final_output, question_lookup)
    map_sec32_2(final_output, question_lookup)
    map_sec32_3(final_output, question_lookup)
    map_sec32_4(final_output, question_lookup, normalized)
    map_sec32_5(final_output, question_lookup)
    map_sec32_6(final_output, question_lookup, normalized)
    _apply_yes_no_fallback(final_output, question_lookup)

    # Step 2 — scope filter: clear answers for questions outside this matter's scope
    if scope is not None:
        _apply_scope(output, scope)

    # Step 3 — client policy overrides: always win, applied after scope
    _apply_client_policy_overrides(question_lookup, client_overrides)

    report = build_report(output, normalized)
    return output, report
