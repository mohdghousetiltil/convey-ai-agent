from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    VENDOR_FORM = "vendor_form"
    SEC32_TAB = "sec32_tab"
    VIC_TITLE = "vic_title"
    PLANNING_REPORT = "planning_report"
    PROPERTY_REPORT = "property_report"
    UNKNOWN = "unknown"


class InputFileType(str, Enum):
    PDF = "pdf"
    WORD = "word"
    YAML = "yaml"
    UNKNOWN = "unknown"


class SourceSpan(BaseModel):
    page: int | None = None
    section: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    label: str | None = None


class FieldPosition(BaseModel):
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None
    width: float | None = None
    height: float | None = None


class DocumentPage(BaseModel):
    page_number: int
    text: str = ""
    normalized_text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class YamlField(BaseModel):
    control_type: str | None = None
    auto_id: str | None = None
    name: str | None = None
    class_name: str | None = None
    current_value: Any = None
    nearby_label: str | None = None
    is_enabled: bool | None = None
    is_checked: bool | None = None
    dropdown_options: list[str] = Field(default_factory=list)
    position: FieldPosition | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Document(BaseModel):
    source_path: Path
    filename: str
    file_type: InputFileType
    document_type: DocumentType = DocumentType.UNKNOWN
    classification_confidence: float = 0.0
    classification_reasons: list[str] = Field(default_factory=list)

    raw_text: str = ""
    normalized_text: str = ""
    pages: list[DocumentPage] = Field(default_factory=list)

    yaml_tab_name: str | None = None
    yaml_total_fields: int | None = None
    yaml_fields: list[YamlField] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_pdf(self) -> bool:
        return self.file_type == InputFileType.PDF

    @property
    def is_yaml(self) -> bool:
        return self.file_type == InputFileType.YAML
