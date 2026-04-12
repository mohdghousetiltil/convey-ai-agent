from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FieldCategory(str, Enum):
    VENDOR_CORE = "vendor_core"
    TRUSTEE = "trustee"
    PROPERTY_DETAILS = "property_details"
    SERVICES_CONNECTED = "services_connected"
    RATES_TAXES_CHARGES = "rates_taxes_charges"
    PLANNING_BUILDING_PERMITS = "planning_building_permits"
    VIC_TITLE_EXTRACT = "vic_title_extract"
    SECTION32_QUESTION = "section32_question"
    SKIP = "skip"


class FieldRule(str, Enum):
    REQUIRED_IF_FOUND = "required_if_found"
    OPTIONAL = "optional"
    DERIVED = "derived"
    UNANSWERED_ALLOWED = "unanswered_allowed"
    SKIP = "skip"


class CanonicalField(BaseModel):
    key: str
    category: FieldCategory
    rules: list[FieldRule] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    notes: str | None = None
