"""Pydantic models for grounded AI extraction results.

Every AI-extracted field must carry a verbatim quote from the source document.
Code verifies the quote exists in the raw text before trusting the value.
If the quote is missing or unverified, effective_confidence() heavily penalises
the result so the reconciler falls back to rules-based extraction.

Three-tier confidence:
  quote present + verified  → full AI confidence (e.g. 0.92)
  quote present + unverified → confidence × 0.10  (likely hallucinated)
  quote absent              → confidence × 0.40  (AI guessed without citing)
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core grounded-value type
# ---------------------------------------------------------------------------

class GroundedValue(BaseModel):
    """A single field extracted by AI with a mandatory evidence quote."""

    value: Any = None
    quote: str | None = None          # verbatim substring from source document
    confidence: float = 0.0
    quote_verified: bool = False      # set by code — never trusted from AI

    def effective_confidence(self) -> float:
        """Return confidence after penalising missing / unverified quotes."""
        if self.value is None:
            return 0.0
        if self.quote is None:
            return self.confidence * 0.40     # no citation provided
        if not self.quote_verified:
            return self.confidence * 0.10     # citation doesn't match document text
        return self.confidence

    @classmethod
    def null(cls) -> "GroundedValue":
        return cls(value=None, quote=None, confidence=0.0, quote_verified=False)


# ---------------------------------------------------------------------------
# Vendor form
# ---------------------------------------------------------------------------

class AIServicesExtract(BaseModel):
    mains_electricity: GroundedValue = Field(default_factory=GroundedValue)
    mains_gas:         GroundedValue = Field(default_factory=GroundedValue)
    mains_water:       GroundedValue = Field(default_factory=GroundedValue)
    mains_sewerage:    GroundedValue = Field(default_factory=GroundedValue)
    telephone_or_nbn:  GroundedValue = Field(default_factory=GroundedValue)
    bottled_gas:       GroundedValue = Field(default_factory=GroundedValue)
    tank_water:        GroundedValue = Field(default_factory=GroundedValue)
    septic_tank:       GroundedValue = Field(default_factory=GroundedValue)


class AIVendorFormExtract(BaseModel):
    services: AIServicesExtract = Field(default_factory=AIServicesExtract)

    # Rates & authorities
    council:       GroundedValue = Field(default_factory=GroundedValue)
    water_authority: GroundedValue = Field(default_factory=GroundedValue)
    council_rates: GroundedValue = Field(default_factory=GroundedValue)
    water_rates:   GroundedValue = Field(default_factory=GroundedValue)

    # Disclosures
    owners_corporation:          GroundedValue = Field(default_factory=GroundedValue)
    insured:                     GroundedValue = Field(default_factory=GroundedValue)
    bushfire_prone_area:         GroundedValue = Field(default_factory=GroundedValue)
    gaic_trigger:                GroundedValue = Field(default_factory=GroundedValue)
    building_permits_last_7_years: GroundedValue = Field(default_factory=GroundedValue)
    improved_past_6_5_years:     GroundedValue = Field(default_factory=GroundedValue)


# ---------------------------------------------------------------------------
# VIC title
# ---------------------------------------------------------------------------

class AITitleExtract(BaseModel):
    volume_folio:          GroundedValue = Field(default_factory=GroundedValue)
    plan_number:           GroundedValue = Field(default_factory=GroundedValue)
    plan_type:             GroundedValue = Field(default_factory=GroundedValue)
    registered_proprietor: GroundedValue = Field(default_factory=GroundedValue)
    lot_description:       GroundedValue = Field(default_factory=GroundedValue)


# ---------------------------------------------------------------------------
# Rates / authority certificates
# ---------------------------------------------------------------------------

class AIRatesCertExtract(BaseModel):
    authority_name:    GroundedValue = Field(default_factory=GroundedValue)
    property_address:  GroundedValue = Field(default_factory=GroundedValue)
    annual_amount:     GroundedValue = Field(default_factory=GroundedValue)
    certificate_date:  GroundedValue = Field(default_factory=GroundedValue)


# ---------------------------------------------------------------------------
# Per-document container
# ---------------------------------------------------------------------------

class AIDocumentExtract(BaseModel):
    source_file:   str
    document_type: str

    vendor_form: AIVendorFormExtract | None = None
    vic_title:   AITitleExtract | None = None
    rates_cert:  AIRatesCertExtract | None = None

    # Populated after quote verification
    fields_total:    int = 0
    fields_verified: int = 0
    fields_rejected: int = 0   # quotes that didn't appear in document text
    extraction_error: str | None = None
