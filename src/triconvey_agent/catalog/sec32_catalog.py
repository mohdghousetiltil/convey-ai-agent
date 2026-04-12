from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

QuestionStatus = Literal["required_if_found", "optional", "skip", "unanswered_allowed", "derived"]


@dataclass(frozen=True)
class Section32Question:
    question: str
    canonical_key: str | None
    status: QuestionStatus
    section: str


SECTION32_QUESTION_CATALOG: list[Section32Question] = [
    Section32Question(
        question="Is the land in a bushfire prone area?",
        canonical_key="bushfire_prone_area",
        status="required_if_found",
        section="planning_building_permits",
    ),
    Section32Question(
        question="Is the land affected by any planning overlays?",
        canonical_key="planning_overlays_affecting_land",
        status="required_if_found",
        section="planning_building_permits",
    ),
    Section32Question(
        question="Is there access to the property by road?",
        canonical_key="road_access",
        status="required_if_found",
        section="planning_building_permits",
    ),
    Section32Question(
        question="Do you pay land tax?",
        canonical_key="land_tax_payable",
        status="required_if_found",
        section="rates_taxes_charges",
    ),
    Section32Question(
        question="Is the Property held by a Trust?",
        canonical_key="is_trust",
        status="required_if_found",
        section="trustee",
    ),
    Section32Question(
        question="Have any planning and/or town planning permit(s) been issued or refused?",
        canonical_key="planning_permits_issued_or_refused",
        status="required_if_found",
        section="planning_building_permits",
    ),
    Section32Question(
        question="Has there been any building works/structures undertaken in the last 7 years?",
        canonical_key="building_works_last_7_years",
        status="required_if_found",
        section="planning_building_permits",
    ),
    Section32Question(
        question="Have any building permits been issued in the last 7 years?",
        canonical_key="building_permits_last_7_years",
        status="required_if_found",
        section="planning_building_permits",
    ),
    Section32Question(
        question="Have you done any works without obtaining a permit?",
        canonical_key="works_without_permit",
        status="required_if_found",
        section="planning_building_permits",
    ),
    Section32Question(
        question="Does the property have a water tank?",
        canonical_key="property_has_water_tank",
        status="optional",
        section="planning_building_permits",
    ),
    Section32Question(
        question="Is any vacant land residential land tax payable?",
        canonical_key="vacant_land_residential_land_tax_payable",
        status="optional",
        section="rates_taxes_charges",
    ),
    Section32Question(
        question="Are there any declared noxious weeds on the land?",
        canonical_key=None,
        status="optional",
        section="section32_questions",
    ),
    Section32Question(
        question="Are there any declared pest animals on the land?",
        canonical_key=None,
        status="optional",
        section="section32_questions",
    ),
    Section32Question(
        question="Emergency Contact",
        canonical_key=None,
        status="skip",
        section="skip",
    ),
    Section32Question(
        question="Real Estate Agent",
        canonical_key=None,
        status="skip",
        section="skip",
    ),
    Section32Question(
        question="Acknowledgment",
        canonical_key=None,
        status="skip",
        section="skip",
    ),
]
