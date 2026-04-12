from __future__ import annotations

from collections import defaultdict
from typing import Any

from triconvey_agent.resolve.conflicts import detect_conflicts
from triconvey_agent.resolve.precedence import source_rank
from triconvey_agent.schemas.documents import Document, DocumentType
from triconvey_agent.schemas.extracted import FieldValue, FinalExtraction

SECTION_FIELD_MAP = {
    "vendor_core": {
        "title",
        "first_name",
        "middle_name",
        "last_name",
        "preferred_name",
        "occupation",
        "residential_address",
        "mobile",
        "email",
        "country_of_citizenship",
        "nationality",
    },
    "trustee": {
        "is_trust",
        "trust_name",
        "trust_type",
        "beneficiaries",
        "trust_abn",
        "trust_deed_uploaded",
    },
    "property_details": {
        "property_address",
        "nature_of_property",
        "approx_building_age",
        "volume_or_book",
        "folio_or_number",
        "lot_number",
        "plan_number",
        "owner_occupied",
        "leased",
        "insured",
        "area_sqm",
        "area_ha",
        "perimeter_m",
        "parcel_count_text",
        "address_candidates",
        "parcel_details_raw",
        "council_property_number",
        "legislative_council",
        "legislative_assembly",
        "local_government_area",
    },
    "services_connected": {
        "mains_electricity",
        "mains_gas",
        "mains_water",
        "sewerage",
        "bottled_gas",
        "tank_water",
        "septic_tank",
        "telephone",
        "nbn",
        "utilities",
    },
    "rates_taxes_charges": {
        "council",
        "council_rates",
        "water_authority",
        "water_rates",
        "land_tax_payable",
        "vacant_land_residential_land_tax_payable",
        "owners_corporation",
    },
    "planning_building_permits": {
        "bushfire_prone_area",
        "planning_overlays",
        "road_access",
        "gaic_trigger",
        "planning_permits_issued_or_refused",
        "building_works_last_7_years",
        "building_permits_last_7_years",
        "works_without_permit",
        "building_work_without_required_approvals",
        "improved_past_6_5_years",
        "improvement_details",
        "new_appliance_details",
        "new_appliances_with_gas_electrical_plumbing_work",
        "compliance_certificates_obtained",
        "property_has_water_tank",
        "permit_required_for_water_tank",
        "planning_overlays_affecting_land",
        "planning_overlays_nearby",
        "planning_zones",
        "planning_scheme",
        "aboriginal_cultural_heritage_sensitivity",
        "fire_authority",
        "registered_aboriginal_party",
    },
    "vic_title_extract": {
        "primary_volume_folio",
        "security_no",
        "produced_date",
        "land_description",
        "parent_title",
        "created_by_instrument",
        "registered_proprietor",
        "plan_type",
        "plan_number",
        "encumbrances_caveats_notices",
        "diagram_location",
        "ect_control",
        "ect_effective_from",
    },
}


def _pick_best(field_key: str, candidates: list[tuple[DocumentType, str, FieldValue]]) -> FieldValue:
    ordered = sorted(
        candidates,
        key=lambda item: (
            source_rank(field_key, item[0]),
            -float(item[2].confidence),
        ),
    )
    best = ordered[0][2].model_copy(deep=True)
    best.conflicts = detect_conflicts(candidates, field_key=field_key, chosen_value=best.value)
    best.requires_review = bool(best.conflicts)
    return best


def _merge_section32_questions(final: FinalExtraction, questions_payload: dict[str, Any]) -> None:
    if not isinstance(questions_payload, dict):
        return
    for bucket_name in ("yes_questions", "no_questions", "unanswered_or_not_found"):
        bucket = questions_payload.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        getattr(final.section32_questions, bucket_name).update(bucket)


def merge_sections(
    extracted_by_doc: list[tuple[Document, dict[str, dict[str, FieldValue]] | dict[str, FieldValue]]]
) -> FinalExtraction:
    final = FinalExtraction()
    bucketed: dict[str, list[tuple[DocumentType, str, FieldValue]]] = defaultdict(list)

    for doc, payload in extracted_by_doc:
        if not payload:
            continue

        for section_name, section_payload in payload.items():
            if section_name == "section32_questions":
                _merge_section32_questions(final, section_payload if isinstance(section_payload, dict) else {})
                continue

            if not isinstance(section_payload, dict):
                continue

            for field_key, field_value in section_payload.items():
                if isinstance(field_value, FieldValue):
                    bucketed[field_key].append((doc.document_type, doc.filename, field_value))

        if all(isinstance(value, FieldValue) for value in payload.values()):
            for field_key, field_value in payload.items():
                bucketed[field_key].append((doc.document_type, doc.filename, field_value))

    for section_name, section_fields in SECTION_FIELD_MAP.items():
        target_section = getattr(final, section_name)
        for field_key in section_fields:
            candidates = bucketed.get(field_key, [])
            if not candidates:
                continue
            target_section[field_key] = _pick_best(field_key, candidates)

    return final
