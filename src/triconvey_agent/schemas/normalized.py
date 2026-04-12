from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NormalizedAttachment(BaseModel):
    doc_type: str
    display_text: str
    exists_as_file: bool
    source: Literal["uploaded_file", "title_reference_only", "placeholder", "inferred"]
    source_file: str | None = None
    confidence: float | None = None
    reference_number: str | None = None
    review_note: str | None = None


class AlternativeServices(BaseModel):
    bottled_gas: bool | None = None
    tank_water: bool | None = None
    septic_tank: bool | None = None


class NormalizedServices(BaseModel):
    electricity_supply_not_connected: bool | None = None
    gas_supply_not_connected: bool | None = None
    water_supply_not_connected: bool | None = None
    sewerage_not_connected: bool | None = None
    telephone_not_connected: bool | None = None
    alternative_services: AlternativeServices = Field(default_factory=AlternativeServices)
    reasoning_notes: list[str] = Field(default_factory=list)


class NormalizedOutput(BaseModel):
    artifact_version: str = "1.0"
    attachments_normalized: list[NormalizedAttachment] = Field(default_factory=list)
    services_normalized: NormalizedServices = Field(default_factory=NormalizedServices)
    review_flags: list[dict[str, str]] = Field(default_factory=list)
    ai_notes: list[dict[str, str]] = Field(default_factory=list)
