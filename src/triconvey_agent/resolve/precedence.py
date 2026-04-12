from __future__ import annotations

from triconvey_agent.schemas.documents import DocumentType

FIELD_PRECEDENCE: dict[str, list[DocumentType]] = {
    "primary_volume_folio": [DocumentType.VIC_TITLE],
    "security_no": [DocumentType.VIC_TITLE],
    "produced_date": [DocumentType.VIC_TITLE],
    "plan_type": [DocumentType.VIC_TITLE],
    "plan_number": [DocumentType.VIC_TITLE],
    "registered_proprietor": [DocumentType.VIC_TITLE],
    "encumbrances_caveats_notices": [DocumentType.VIC_TITLE],
    "land_description": [DocumentType.VIC_TITLE],
    "parent_title": [DocumentType.VIC_TITLE],
    "created_by_instrument": [DocumentType.VIC_TITLE],
    "diagram_location": [DocumentType.VIC_TITLE],
    "ect_control": [DocumentType.VIC_TITLE],
    "ect_effective_from": [DocumentType.VIC_TITLE],
    "bushfire_prone_area": [DocumentType.PLANNING_REPORT, DocumentType.VENDOR_FORM],
    "planning_overlays_affecting_land": [DocumentType.PLANNING_REPORT],
    "planning_overlays_nearby": [DocumentType.PLANNING_REPORT],
    "planning_zones": [DocumentType.PLANNING_REPORT],
    "planning_scheme": [DocumentType.PLANNING_REPORT],
    "fire_authority": [DocumentType.PLANNING_REPORT],
    "registered_aboriginal_party": [DocumentType.PLANNING_REPORT],
    "aboriginal_cultural_heritage_sensitivity": [DocumentType.PLANNING_REPORT],
    "area_sqm": [DocumentType.PROPERTY_REPORT],
    "area_ha": [DocumentType.PROPERTY_REPORT],
    "perimeter_m": [DocumentType.PROPERTY_REPORT],
    "parcel_details_raw": [DocumentType.PROPERTY_REPORT],
    "address_candidates": [DocumentType.PROPERTY_REPORT],
    "parcel_count_text": [DocumentType.PROPERTY_REPORT],
    "council_property_number": [DocumentType.PROPERTY_REPORT],
    "legislative_council": [DocumentType.PROPERTY_REPORT, DocumentType.PLANNING_REPORT],
    "legislative_assembly": [DocumentType.PROPERTY_REPORT, DocumentType.PLANNING_REPORT],
    "first_name": [DocumentType.VENDOR_FORM],
    "middle_name": [DocumentType.VENDOR_FORM],
    "last_name": [DocumentType.VENDOR_FORM],
    "preferred_name": [DocumentType.VENDOR_FORM],
    "mobile": [DocumentType.VENDOR_FORM],
    "email": [DocumentType.VENDOR_FORM],
    "country_of_citizenship": [DocumentType.VENDOR_FORM],
    "nationality": [DocumentType.VENDOR_FORM],
    "trust_name": [DocumentType.VENDOR_FORM],
    "trust_type": [DocumentType.VENDOR_FORM],
    "beneficiaries": [DocumentType.VENDOR_FORM],
    "trust_abn": [DocumentType.VENDOR_FORM],
    "property_address": [DocumentType.VENDOR_FORM, DocumentType.PROPERTY_REPORT],
    "council": [DocumentType.VENDOR_FORM],
    "council_rates": [DocumentType.VENDOR_FORM],
    "water_authority": [DocumentType.VENDOR_FORM, DocumentType.PROPERTY_REPORT, DocumentType.PLANNING_REPORT],
    "water_rates": [DocumentType.VENDOR_FORM],
    "road_access": [DocumentType.VENDOR_FORM],
}


def source_rank(field_key: str, doc_type: DocumentType) -> int:
    order = FIELD_PRECEDENCE.get(field_key, [])
    try:
        return order.index(doc_type)
    except ValueError:
        return 999
