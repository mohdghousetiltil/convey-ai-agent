from __future__ import annotations

from pydantic import BaseModel, Field

from triconvey_agent.schemas.extracted import FieldValue, FinalExtraction


class QualityReviewItem(BaseModel):
    section: str
    field_key: str
    reason: str
    value_preview: str | None = None


class QualityReviewReport(BaseModel):
    items: list[QualityReviewItem] = Field(default_factory=list)


DIRTY_SNIPPET_MARKERS = [
    "Property Details",
    "Please provide full particulars",
    "Name of Trust",
    "Was a permit required",
]


def _preview(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:180]


def _check_field(section_name: str, key: str, field: FieldValue, report: QualityReviewReport) -> None:
    if not field.evidence:
        return

    snippet = field.evidence[0].snippet or ""
    label = field.evidence[0].label or ""

    if key == "permit_required_for_water_tank":
        lines = [line.strip() for line in snippet.splitlines() if line.strip()]
        if len(lines) > 2:
            report.items.append(
                QualityReviewItem(
                    section=section_name,
                    field_key=key,
                    reason="Snippet appears to include more than the expected label and answer.",
                    value_preview=_preview(field.value),
                )
            )
        return

    if isinstance(field.value, str) and len(field.value.strip()) <= 4 and key in {"new_appliance_details", "improvement_details"}:
        report.items.append(
            QualityReviewItem(
                section=section_name,
                field_key=key,
                reason="Value looks too short for an expected descriptive field.",
                value_preview=_preview(field.value),
            )
        )

    if key == "new_appliance_details" and isinstance(field.value, dict):
        if field.value.get("installed_year") and not field.value.get("item"):
            report.items.append(
                QualityReviewItem(
                    section=section_name,
                    field_key=key,
                    reason="Narrative was reduced to a year with no identifiable appliance item.",
                    value_preview=_preview(field.value),
                )
            )
        return

    for marker in DIRTY_SNIPPET_MARKERS:
        if marker in snippet and marker != label and key not in {"property_address"}:
            report.items.append(
                QualityReviewItem(
                    section=section_name,
                    field_key=key,
                    reason=f"Snippet may include adjacent label/content: {marker}",
                    value_preview=_preview(field.value),
                )
            )
            break


def build_quality_review_report(extraction: FinalExtraction) -> QualityReviewReport:
    report = QualityReviewReport()

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
        for key, field in section.items():
            _check_field(section_name, key, field, report)

    return report
