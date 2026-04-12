from __future__ import annotations

from triconvey_agent.extractors.common import iter_page_text, make_field
from triconvey_agent.extractors.form_parser import extract_field_after_label, extract_question_answer
from triconvey_agent.ingest.normalizer import normalize_scalar
from triconvey_agent.schemas.documents import Document
from triconvey_agent.schemas.extracted import FieldValue

ExtractorMap = dict[str, tuple[str, ...]]

LABEL_MAP: dict[str, ExtractorMap] = {
    "vendor_core": {
        "title": ("What is your title?",),
        "first_name": ("Your first name",),
        "middle_name": ("Your middle name",),
        "last_name": ("Your last name",),
        "preferred_name": ("Preferred Name",),
        "occupation": ("Occupation",),
        "residential_address": ("Residential address",),
        "mobile": ("Contact Number", "Mobile", "Phone Number"),
        "email": ("Email Address", "Email"),
        "country_of_citizenship": ("Country of citizenship",),
        "nationality": ("Nationality",),
    },
    "trustee": {
        "trust_name": ("Name of Trust",),
        "trust_type": ("Please confirm Trust Type", "Trust Type"),
        "beneficiaries": ("Who are the beneficiaries?",),
        "trust_abn": ("Trust ABN",),
        "trust_deed_uploaded": ("Please upload a copy of the Trust Deed",),
    },
    "property_details": {
        "property_address": ("Property Address",),
        "nature_of_property": ("Nature of property",),
        "approx_building_age": ("Approximate age of Dwelling / Building",),
        "volume_or_book": ("Volume or book:",),
        "folio_or_number": ("Folio or number:",),
        "lot_number": ("Lot number",),
        "plan_number": ("Plan number",),
    },
    "rates_taxes_charges": {
        "council": ("Council",),
        "council_rates": ("Council rates",),
        "water_authority": ("Water Authority",),
        "water_rates": ("Water rates",),
    },
}

BOOLEAN_QUESTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "trustee": {
        "is_trust": ("Is the Property held by a Trust?",),
    },
    "property_details": {
        "owner_occupied": ("Do you live at the property?",),
        "leased": ("Is the Property rented/leased?",),
        "insured": ("Is the property insured?",),
    },
    "rates_taxes_charges": {
        "land_tax_payable": ("Do you pay land tax?",),
        "vacant_land_residential_land_tax_payable": ("Is any vacant land residential land tax payable?",),
        "owners_corporation": (
            "If your property affected by an Owners Corporation otherwise known as Strata Insurance for common property.",
        ),
    },
    "planning_building_permits": {
        "bushfire_prone_area": ("Is the land in a bushfire prone area?",),
        "planning_overlays": ("Is the land affected by any planning overlays?",),
        "road_access": ("Is there access to the property by road?",),
        "gaic_trigger": ('Will the sale of the land trigger payment of the "Growth Area Infrastructure Contribution?',),
        "property_less_than_7_years_old": ("Is this property less than 7 years old?",),
        "planning_permits_issued_or_refused": ("Have any planning and/or town planning permit(s) been issued or refused?",),
        "building_works_last_7_years": ("Has there been any building works/structures undertaken in the last 7 years?",),
        "building_permits_last_7_years": ("Have any building permits been issued in the last 7 years?",),
        "works_without_permit": ("Have you done any works without obtaining a permit?",),
        "building_work_without_required_approvals": (
            "Has any building work been done on the land that DID NOT have the required building nor planning approvals?",
        ),
        "improved_past_6_5_years": ("Have you improved the property within the past 6.5 years?",),
        "new_appliances_with_gas_electrical_plumbing_work": (
            "Have you installed new appliances where works relating to gas, electrical and plumbing have been undertaken?",
        ),
        "compliance_certificates_obtained": (
            "Have you obtained the Compliance certificates - as per S.137 (c) of the Building Act",
        ),
        "property_has_water_tank": ("Does the property have a water tank?",),
        "permit_required_for_water_tank": ("Was a permit required for the water tank",),
    },
}

DETAIL_FIELDS: dict[str, tuple[tuple[str, ...], str]] = {
    "improvement_details": (
        (
            "If yes, provide a detailed explanation of the works completed (what works have been done to the property), itemise the cost of each of the works and the date of completion of each of the works (PLEASE NOTE THAT FAILURE TO PROVIDE DETAIL OF THE WORKS COMPLETED, COST OF EACH OF THE WORKS & DATE OF COMPLETION OF EACH OF THE WORKS MAY BE THE CAUSE OF DELAY FOR OUR OFFICE TO PREPARE THE SECTION 32 FOR YOU)",
        ),
        "planning_building_permits",
    ),
    "new_appliance_details": (
        (
            "If you have installed new appliances (gas, electrical or plumbing), please specify what appliances were installed, when the appliance was installed & what the cost was.",
        ),
        "planning_building_permits",
    ),
    "ancillary_outbuildings_details": (
        ("If yes, please advise on the ancillary outbuildings.",),
        "property_details",
    ),
}

# Maps output key → (display label, case-insensitive substrings to detect in a PDF line).
# More specific entries first to prevent "sewerage" from greedily matching "mains sewerage" before
# the mains_sewerage key is processed (both can match the same line — that is intentional and safe).
_SERVICE_VARIANTS: list[tuple[str, str, list[str]]] = [
    ("mains_electricity", "Mains Electricity", ["mains electricity"]),
    ("mains_gas",         "Mains Gas",         ["mains gas"]),
    ("mains_water",       "Mains Water",        ["mains water"]),
    ("mains_sewerage",    "Mains Sewerage",     ["mains sewerage"]),
    ("septic_tank",       "Septic Tank",        ["septic tank"]),
    ("bottled_gas",       "Bottled Gas",        ["bottled gas", "lpg"]),
    ("tank_water",        "Tank Water",         ["tank water"]),
    ("sewerage",          "Sewerage",           ["sewerage"]),   # fallback for forms using plain "Sewerage"
    ("telephone",         "Telephone",          ["telephone"]),
    ("nbn",               "NBN",                ["nbn", "voip"]),  # also matches "VOIP phone via NBN"
]

ALL_VENDOR_LABELS = [
    "What is your title?",
    "Your first name",
    "Your middle name",
    "Your last name",
    "Preferred Name",
    "Occupation",
    "Residential address",
    "Contact Number",
    "Email Address",
    "Birth date",
    "Gender",
    "Have we acted for you before?",
    "How did you hear about us?",
    "Country of citizenship",
    "Nationality",
    "Is the Property held by a Trust?",
    "Name of Trust",
    "Please confirm Trust Type",
    "Who are the beneficiaries?",
    "Trust ABN",
    "Please upload a copy of the Trust Deed",
    "Property Address",
    "Nature of property",
    "Approximate age of Dwelling / Building",
    "Volume or book:",
    "Folio or number:",
    "Lot number",
    "Plan number",
    "Do you live at the property?",
    "Is the Property rented/leased?",
    "Is the property insured?",
    "Council",
    "Council rates",
    "Water Authority",
    "Water rates",
    "Do you pay land tax?",
    "Is any vacant land residential land tax payable?",
    "Is the land in a bushfire prone area?",
    "Is the land affected by any planning overlays?",
    "Is there access to the property by road?",
    "Have any planning and/or town planning permit(s) been issued or refused?",
    "Has there been any building works/structures undertaken in the last 7 years?",
    "Have any building permits been issued in the last 7 years?",
    "Have you done any works without obtaining a permit?",
    "Has any building work been done on the land that DID NOT have the required building nor planning approvals?",
    "Have you improved the property within the past 6.5 years?",
    "If yes, provide a detailed explanation of the works completed (what works have been done to the property), itemise the cost of each of the works and the date of completion of each of the works (PLEASE NOTE THAT FAILURE TO PROVIDE DETAIL OF THE WORKS COMPLETED, COST OF EACH OF THE WORKS & DATE OF COMPLETION OF EACH OF THE WORKS MAY BE THE CAUSE OF DELAY FOR OUR OFFICE TO PREPARE THE SECTION 32 FOR YOU)",
    "Have you installed new appliances where works relating to gas, electrical and plumbing have been undertaken?",
    "If you have installed new appliances (gas, electrical or plumbing), please specify what appliances were installed, when the appliance was installed & what the cost was.",
    "Have you obtained the Compliance certificates - as per S.137 (c) of the Building Act",
    "Does the property have a water tank?",
    "Was a permit required for the water tank",
    "Please provide full particulars of your insurance cover:",
    "Please upload Certificate of Currency",
    "If yes, please specify what works were completed without obtaining a permit & when they were completed:",
]


def _find_value_for_label(doc: Document, label: str):
    for page_number, text in iter_page_text(doc):
        match = extract_field_after_label(
            text,
            label,
            known_labels=ALL_VENDOR_LABELS,
            max_lines=8,
        )
        if match:
            value = normalize_scalar(match.value)
            return value, page_number, match.start_line, match.end_line
    return None, None, None, None


def _extract_bool_question(doc: Document, question: str):
    for page_number, text in iter_page_text(doc):
        match = extract_question_answer(
            text,
            question,
            known_labels=ALL_VENDOR_LABELS,
            max_lines=2,
        )
        if not match:
            continue

        raw_answer = normalize_scalar(match.value)
        if raw_answer is None:
            return None, page_number, None, match.start_line, match.end_line

        answer = str(raw_answer).strip().lower()
        if answer.startswith("yes"):
            return True, page_number, raw_answer, match.start_line, match.end_line
        if answer.startswith("no"):
            return False, page_number, raw_answer, match.start_line, match.end_line
        return None, page_number, raw_answer, match.start_line, match.end_line

    return None, None, None, None, None


def _make_labeled_fields(
    doc: Document,
    section_name: str,
    mapping: ExtractorMap,
    extractor_name: str,
) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}
    for key, labels in mapping.items():
        for label in labels:
            value, page_number, line_start, line_end = _find_value_for_label(doc, label)
            if value is None:
                continue
            result[key] = make_field(
                value=value,
                document=doc,
                extractor=extractor_name,
                normalized_key=key,
                page=page_number,
                section=section_name,
                snippet=f"{label}\n{value}",
                label=label,
                line_start=line_start,
                line_end=line_end,
                confidence=0.97,
            )
            break
    return result


def _make_boolean_fields(
    doc: Document,
    section_name: str,
    mapping: dict[str, tuple[str, ...]],
    extractor_name: str,
) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}
    for key, questions in mapping.items():
        for question in questions:
            value, page_number, raw_answer, line_start, line_end = _extract_bool_question(doc, question)
            if raw_answer is None:
                continue
            result[key] = make_field(
                value=value,
                document=doc,
                extractor=extractor_name,
                normalized_key=key,
                page=page_number,
                section=section_name,
                snippet=f"{question}\n{raw_answer}",
                label=question,
                line_start=line_start,
                line_end=line_end,
                confidence=0.97,
            )
            break
    return result


def _extract_detail_field(doc: Document, section_name: str, key: str, labels: tuple[str, ...]) -> FieldValue | None:
    for label in labels:
        value, page_number, line_start, line_end = _find_value_for_label(doc, label)
        if value is None:
            continue
        return make_field(
            value=value,
            document=doc,
            extractor="vendor_form_extractor",
            normalized_key=key,
            page=page_number,
            section=section_name,
            snippet=f"{label}\n{value}",
            label=label,
            line_start=line_start,
            line_end=line_end,
            confidence=0.88,
        )
    return None


def _extract_services_connected(doc: Document) -> dict[str, FieldValue]:
    result: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        if "Are the services listed connected to the land?" not in text:
            continue

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        checklist_snippet = "\n".join(lines)

        for key, label, variants in _SERVICE_VARIANTS:
            # Find the first line (1-indexed) that contains any variant substring
            # (case-insensitive). A line like "VOIP phone via NBN" matches "nbn" and "voip".
            matched_line: int | None = None
            for idx, line in enumerate(lines, start=1):
                line_lower = line.lower()
                if any(v in line_lower for v in variants):
                    matched_line = idx
                    break

            result[key] = make_field(
                value=matched_line is not None,
                document=doc,
                extractor="vendor_form_extractor",
                normalized_key=key,
                page=page_number,
                section="Services Connected",
                snippet=checklist_snippet,
                label=label,
                line_start=matched_line,
                line_end=matched_line,
                confidence=0.92 if matched_line is not None else 0.85,
            )
        break

    return result


def extract_vendor_form_sections(doc: Document) -> dict[str, dict[str, FieldValue]]:
    vendor_core = _make_labeled_fields(doc, "Your details", LABEL_MAP["vendor_core"], "vendor_form_extractor")
    trustee = _make_labeled_fields(doc, "Trustee Questions", LABEL_MAP["trustee"], "vendor_form_extractor")
    trustee.update(_make_boolean_fields(doc, "Trustee Questions", BOOLEAN_QUESTIONS["trustee"], "vendor_form_extractor"))

    property_details = _make_labeled_fields(doc, "Property Details", LABEL_MAP["property_details"], "vendor_form_extractor")
    property_details.update(
        _make_boolean_fields(doc, "Property Details", BOOLEAN_QUESTIONS["property_details"], "vendor_form_extractor")
    )

    ancillary_detail = _extract_detail_field(
        doc,
        "Property Details",
        "ancillary_outbuildings_details",
        DETAIL_FIELDS["ancillary_outbuildings_details"][0],
    )
    if ancillary_detail:
        property_details["ancillary_outbuildings_details"] = ancillary_detail

    rates_taxes_charges = _make_labeled_fields(
        doc,
        "Rates, Taxes and Charges",
        LABEL_MAP["rates_taxes_charges"],
        "vendor_form_extractor",
    )
    rates_taxes_charges.update(
        _make_boolean_fields(
            doc,
            "Rates, Taxes and Charges",
            BOOLEAN_QUESTIONS["rates_taxes_charges"],
            "vendor_form_extractor",
        )
    )

    planning_building_permits = _make_boolean_fields(
        doc,
        "Town Planning, Building Approvals and Permits",
        BOOLEAN_QUESTIONS["planning_building_permits"],
        "vendor_form_extractor",
    )
    for detail_key, (labels, section_name) in DETAIL_FIELDS.items():
        if section_name != "planning_building_permits":
            continue
        detail_field = _extract_detail_field(doc, "Town Planning, Building Approvals and Permits", detail_key, labels)
        if detail_field:
            planning_building_permits[detail_key] = detail_field

    services_connected = _extract_services_connected(doc)

    return {
        "vendor_core": vendor_core,
        "trustee": trustee,
        "property_details": property_details,
        "services_connected": services_connected,
        "rates_taxes_charges": rates_taxes_charges,
        "planning_building_permits": planning_building_permits,
    }
