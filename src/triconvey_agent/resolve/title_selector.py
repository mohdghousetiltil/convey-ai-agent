from __future__ import annotations

from pydantic import BaseModel, Field

from triconvey_agent.schemas.documents import Document, DocumentType
from triconvey_agent.schemas.extracted import FieldValue

TITLE_KEYS = {
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
}


class TitleRecord(BaseModel):
    source_file: str
    fields: dict[str, FieldValue] = Field(default_factory=dict)


class TitleSelectionResult(BaseModel):
    primary_title: TitleRecord | None = None
    additional_titles: list[TitleRecord] = Field(default_factory=list)


def _get_primary_volume_folio(payload: dict[str, FieldValue]) -> str:
    field = payload.get("primary_volume_folio")
    if not field or field.value is None:
        return ""
    return str(field.value).strip().upper()


def select_titles(
    extracted_by_doc: list[tuple[Document, dict[str, object]]],
    preferred_volume_folio: str | None = None,
) -> TitleSelectionResult:
    title_records: list[TitleRecord] = []

    for doc, payload in extracted_by_doc:
        if doc.document_type != DocumentType.VIC_TITLE:
            continue
        if not isinstance(payload, dict):
            continue

        title_fields = {
            key: value
            for key, value in payload.items()
            if key in TITLE_KEYS and isinstance(value, FieldValue)
        }
        if title_fields:
            title_records.append(TitleRecord(source_file=doc.filename, fields=title_fields))

    if not title_records:
        return TitleSelectionResult()

    if preferred_volume_folio:
        preferred = preferred_volume_folio.strip().upper()
        for index, record in enumerate(title_records):
            if _get_primary_volume_folio(record.fields) == preferred:
                return TitleSelectionResult(
                    primary_title=record,
                    additional_titles=title_records[:index] + title_records[index + 1 :],
                )

    # No preferred folio supplied — sort by source filename for stable ordering.
    title_records.sort(key=lambda record: record.source_file)

    return TitleSelectionResult(primary_title=title_records[0], additional_titles=title_records[1:])
