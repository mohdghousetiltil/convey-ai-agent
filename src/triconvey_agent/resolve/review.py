from __future__ import annotations

from pydantic import BaseModel, Field

from triconvey_agent.normalizers.address import addresses_equivalent
from triconvey_agent.schemas.extracted import FieldValue, FinalExtraction


class ReviewSummary(BaseModel):
    requires_review: dict[str, dict[str, FieldValue]] = Field(default_factory=dict)
    unresolved: dict[str, list[str]] = Field(default_factory=dict)


def _should_skip_review(section_name: str, key: str, field: FieldValue) -> bool:
    if section_name == "property_details" and key == "property_address" and field.conflicts:
        values = [field.value] + [conflict.value for conflict in field.conflicts]
        string_values = [value for value in values if isinstance(value, str)]
        if len(string_values) >= 2:
            first = string_values[0]
            if all(addresses_equivalent(first, other) for other in string_values[1:]):
                return True
    return False


def build_review_summary(extraction: FinalExtraction) -> ReviewSummary:
    summary = ReviewSummary()

    section_names = [
        "vendor_core",
        "trustee",
        "property_details",
        "services_connected",
        "rates_taxes_charges",
        "planning_building_permits",
        "vic_title_extract",
    ]

    for section_name in section_names:
        section = getattr(extraction, section_name, {})
        flagged: dict[str, FieldValue] = {}
        unresolved: list[str] = []

        for key, field in section.items():
            if getattr(field, "requires_review", False) and not _should_skip_review(section_name, key, field):
                flagged[key] = field
            if field.value is None:
                unresolved.append(key)

        if flagged:
            summary.requires_review[section_name] = flagged
        if unresolved:
            summary.unresolved[section_name] = unresolved

    if extraction.section32_questions.unanswered_or_not_found:
        summary.unresolved["section32_questions"] = list(extraction.section32_questions.unanswered_or_not_found.keys())

    return summary
