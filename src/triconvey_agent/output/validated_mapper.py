from __future__ import annotations

from copy import deepcopy
from typing import Any

from triconvey_agent.output.triconvey_mapper import map_answers


def _get_nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def sanitize_normalized_output(final_output: dict[str, Any], normalized_output: dict[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(normalized_output)
    services = dict(sanitized.get("services_normalized", {}))

    raw_service_paths = {
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

    for field_name, candidate_paths in raw_service_paths.items():
        raw_value = None
        for path in candidate_paths:
            raw_value = _get_nested(final_output, path)
            if raw_value is not None:
                break
        services[field_name] = True if raw_value is False else None

    sanitized["services_normalized"] = services
    return sanitized


def validate_normalized_output(normalized_output: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    attachments = normalized_output.get("attachments_normalized", [])
    for item in attachments:
        if item.get("exists_as_file") is True and not item.get("display_text"):
            issues.append(
                {
                    "field": f"attachment:{item.get('doc_type')}",
                    "reason": "Confirmed uploaded attachment is missing display_text.",
                    "severity": "high",
                }
            )

        if item.get("source") == "title_reference_only" and item.get("exists_as_file") is True:
            issues.append(
                {
                    "field": f"attachment:{item.get('reference_number')}",
                    "reason": "Title-reference-only item cannot also be marked exists_as_file=true.",
                    "severity": "high",
                }
            )

    services = normalized_output.get("services_normalized", {})
    for field_name in [
        "electricity_supply_not_connected",
        "gas_supply_not_connected",
        "water_supply_not_connected",
        "sewerage_not_connected",
        "telephone_not_connected",
    ]:
        if services.get(field_name) is False:
            issues.append(
                {
                    "field": f"services_normalized.{field_name}",
                    "reason": "Inverted service checkbox fields must be true or null, never false.",
                    "severity": "high",
                }
            )

    return issues


def map_answers_with_validation(
    final_output: dict[str, Any], triconvey_template: dict[str, Any], normalized_output: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    sanitized = sanitize_normalized_output(final_output, normalized_output)
    validation_issues = validate_normalized_output(sanitized)
    answered, report = map_answers(final_output, triconvey_template, normalized_output=sanitized)
    report["validator_issues"] = validation_issues
    return answered, report
