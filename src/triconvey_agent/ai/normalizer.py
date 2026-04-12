from __future__ import annotations

import json
import re
from typing import Any

from triconvey_agent.ai.client import AIClient
from triconvey_agent.ai.prompts import build_attachments_normalization_prompt, build_services_normalization_prompt
from triconvey_agent.schemas.normalized import NormalizedOutput


def _get_nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _extract_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    if not match:
        raise ValueError("AI response did not contain valid JSON.")
    return json.loads(match.group(1))


def _build_service_evidence_packet(final_output: dict[str, Any]) -> dict[str, Any]:
    return {
        "mains_electricity": _get_nested(final_output, "services_connected.mains_electricity"),
        "bottled_gas": _get_nested(final_output, "services_connected.bottled_gas"),
        "tank_water": _get_nested(final_output, "services_connected.tank_water"),
        "septic_tank": _get_nested(final_output, "services_connected.septic_tank"),
        "utilities": _get_nested(final_output, "services_connected.utilities"),
    }


def _build_attachment_evidence_packet(final_output: dict[str, Any], baseline_normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_files": _get_nested(final_output, "source_summary.files", []),
        "primary_title": {
            "source_file": _get_nested(final_output, "source_summary.primary_title_source"),
            "volume_folio": _get_nested(final_output, "vic_title_extract.primary_volume_folio.value"),
            "produced_date": _get_nested(final_output, "vic_title_extract.produced_date.value"),
            "diagram_location": _get_nested(final_output, "vic_title_extract.diagram_location.value"),
            "land_description": _get_nested(final_output, "vic_title_extract.land_description.value"),
        },
        "additional_titles": [
            {
                "source_file": title.get("source_file"),
                "volume_folio": _get_nested(title, "fields.primary_volume_folio.value"),
                "produced_date": _get_nested(title, "fields.produced_date.value"),
                "diagram_location": _get_nested(title, "fields.diagram_location.value"),
                "land_description": _get_nested(title, "fields.land_description.value"),
            }
            for title in final_output.get("additional_titles", [])
        ],
        "baseline_confirmed": [
            item for item in baseline_normalized.get("attachments_normalized", []) if item.get("exists_as_file") is True
        ],
        "baseline_referenced": [
            item for item in baseline_normalized.get("attachments_normalized", []) if item.get("exists_as_file") is False
        ],
    }


def _merge_services(baseline: dict[str, Any], ai_services: dict[str, Any]) -> dict[str, Any]:
    merged = dict(baseline)
    for key in [
        "electricity_supply_not_connected",
        "gas_supply_not_connected",
        "water_supply_not_connected",
        "sewerage_not_connected",
        "telephone_not_connected",
    ]:
        candidate = ai_services.get(key)
        merged[key] = True if candidate is True else None

    merged["alternative_services"] = {
        **baseline.get("alternative_services", {}),
        **ai_services.get("alternative_services", {}),
    }
    merged["reasoning_notes"] = list(
        dict.fromkeys(
            list(baseline.get("reasoning_notes", [])) + list(ai_services.get("reasoning_notes", []))
        )
    )
    return merged


def _sanitize_services_with_evidence(final_output: dict[str, Any], services: dict[str, Any]) -> dict[str, Any]:
    """Correct AI service inferences only when raw evidence contradicts them.

    Logic per field:
    - mains value is True  → service IS connected → force not_connected = None
    - mains value is False → service NOT connected → confirm not_connected = True
    - mains value missing  → no contradicting evidence → leave AI/baseline answer alone
    """
    sanitized = dict(services)
    evidence_paths = {
        "electricity_supply_not_connected": [
            "services_connected.mains_electricity.value",
        ],
        "gas_supply_not_connected": [
            "services_connected.mains_gas.value",
            "services_connected.gas_supply.value",
            "services_connected.gas_connected.value",
        ],
        "water_supply_not_connected": [
            "services_connected.mains_water.value",
            "services_connected.water_supply.value",
            "services_connected.water_connected.value",
        ],
        "sewerage_not_connected": [
            "services_connected.mains_sewerage.value",
            "services_connected.sewerage.value",
            "services_connected.sewer_connected.value",
        ],
        "telephone_not_connected": [
            "services_connected.telephone.value",
            "services_connected.telephone_services.value",
            "services_connected.telephone_connected.value",
        ],
    }

    for field_name, candidate_paths in evidence_paths.items():
        found_value = None
        for path in candidate_paths:
            raw = _get_nested(final_output, path)
            if raw is not None:
                found_value = raw
                break

        if found_value is True:
            # Mains service IS connected — cannot be "not connected"
            sanitized[field_name] = None
        elif found_value is False:
            # Mains service explicitly NOT connected — confirm it
            sanitized[field_name] = True
        # else: field not in vendor form — trust the baseline/AI answer unchanged

    return sanitized


def _attachment_allowed(item: dict[str, Any], source_files: list[str]) -> bool:
    if item.get("source") == "uploaded_file":
        source_file = item.get("source_file")
        return bool(source_file and source_file in source_files)
    if item.get("source") == "title_reference_only":
        return item.get("exists_as_file") is False
    if item.get("source") in {"placeholder", "inferred"}:
        return item.get("exists_as_file") is False
    return False


def _merge_attachments(baseline: list[dict[str, Any]], ai_items: list[dict[str, Any]], source_files: list[str]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str | None, str], dict[str, Any]] = {}

    def _key(item: dict[str, Any]) -> tuple[str, str | None, str]:
        return (
            str(item.get("doc_type")),
            item.get("reference_number"),
            str(item.get("display_text")),
        )

    for item in baseline:
        by_key[_key(item)] = dict(item)

    for item in ai_items:
        if not _attachment_allowed(item, source_files):
            continue
        key = _key(item)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = dict(item)
            continue

        # Prefer confirmed uploaded files from baseline; otherwise let AI enrich notes/confidence.
        if existing.get("exists_as_file") is True and existing.get("source") == "uploaded_file":
            continue
        merged = {**existing, **item}
        if merged.get("source") == "uploaded_file" and merged.get("source_file") not in source_files:
            continue
        by_key[key] = merged

    return list(by_key.values())


def run_ai_normalization(final_output: dict[str, Any], baseline_normalized: dict[str, Any], client: AIClient) -> dict[str, Any]:
    services_prompt = build_services_normalization_prompt(
        _build_service_evidence_packet(final_output),
        baseline_normalized.get("services_normalized", {}),
    )
    attachments_prompt = build_attachments_normalization_prompt(
        _build_attachment_evidence_packet(final_output, baseline_normalized),
        baseline_normalized.get("attachments_normalized", []),
    )

    services_response = client.complete(services_prompt)
    attachments_response = client.complete(attachments_prompt)

    try:
        ai_services = _extract_json(services_response.raw_text)
        ai_attachments = _extract_json(attachments_response.raw_text)
    except Exception:
        return {
            "baseline_retained": True,
            "reason": "AI normalization response was not valid JSON.",
        }

    source_files = _get_nested(final_output, "source_summary.files", [])
    merged = {
        **baseline_normalized,
        "services_normalized": _sanitize_services_with_evidence(
            final_output,
            _merge_services(
                baseline_normalized.get("services_normalized", {}),
                ai_services,
            ),
        ),
        "attachments_normalized": _merge_attachments(
            baseline_normalized.get("attachments_normalized", []),
            ai_attachments if isinstance(ai_attachments, list) else [],
            source_files,
        ),
        "ai_notes": [
            {
                "section": "services",
                "status": "merged",
            },
            {
                "section": "attachments",
                "status": "merged",
            },
        ],
    }

    # Preserve baseline review flags and add any AI reasoning flags conservatively.
    merged["review_flags"] = list(
        dict.fromkeys(
            json.dumps(item, sort_keys=True)
            for item in baseline_normalized.get("review_flags", [])
            + list(ai_services.get("review_flags", []))
        )
    )
    merged["review_flags"] = [json.loads(item) for item in merged["review_flags"]]

    validated = NormalizedOutput.model_validate(merged)
    return validated.model_dump(mode="json")
