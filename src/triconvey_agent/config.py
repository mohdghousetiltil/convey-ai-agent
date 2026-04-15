from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from triconvey_agent.schemas.documents import DocumentType


class DocumentSignature(BaseModel):
    document_type: DocumentType
    filename_terms: tuple[str, ...] = ()
    text_terms: tuple[str, ...] = ()
    tab_name_terms: tuple[str, ...] = ()
    required_term_groups: tuple[tuple[str, ...], ...] = ()
    minimum_score: float = 3.0


class Settings(BaseModel):
    app_name: str = "triconvey-agent"
    default_encoding: str = "utf-8"
    execution_artifact_retention_hours: int = 72
    execution_artifact_keep_latest: int = 10
    supported_pdf_suffixes: tuple[str, ...] = (".pdf",)
    supported_yaml_suffixes: tuple[str, ...] = (".yaml", ".yml")
    supported_input_suffixes: tuple[str, ...] = (".pdf", ".yaml", ".yml")

    filename_term_weight: float = 2.5
    text_term_weight: float = 1.5
    tab_name_term_weight: float = 2.0
    required_group_weight: float = 2.25
    ambiguity_penalty: float = 0.5

    output_filename: str = "extraction_result.json"
    debug_output_filename: str = "ingest_debug.json"

    skip_sections: tuple[str, ...] = (
        "Emergency Contact",
        "Real Estate Agent",
        "Acknowledgment",
        "Acknowledgement",
    )

    document_signatures: tuple[DocumentSignature, ...] = (
        DocumentSignature(
            document_type=DocumentType.VENDOR_FORM,
            filename_terms=(
                "vendor instruction",
                "vendor instructions",
                "vendor authority",
            ),
            text_terms=(
                "vendor instruction form",
                "vendor details",
                "trustee",
                "services connected",
                "rates charges",
            ),
            required_term_groups=(
                ("vendor", "instruction"),
                ("vendor", "details"),
            ),
        ),
        DocumentSignature(
            document_type=DocumentType.SEC32_TAB,
            filename_terms=("sec 32", "sec32", "tab sec 32"),
            tab_name_terms=("sec 32",),
            text_terms=(
                "owners corporation",
                "planning overlay",
                "land tax",
                "building energy efficiency",
            ),
            required_term_groups=(("sec", "32"),),
        ),
        DocumentSignature(
            document_type=DocumentType.VIC_TITLE,
            filename_terms=(
                "register search statement",
                "copy of title",
                "volume",
                "folio",
            ),
            text_terms=(
                "register search statement",
                "copy of title",
                "registered proprietor",
                "land description",
                "security no",
            ),
            required_term_groups=(
                ("volume", "folio"),
                ("register", "statement"),
            ),
        ),
        DocumentSignature(
            document_type=DocumentType.PLANNING_REPORT,
            filename_terms=("planning report", "vicplan", "planning property report"),
            text_terms=(
                "planning property report",
                "planning overlay",
                "zone",
                "bushfire",
            ),
            required_term_groups=(
                ("planning", "report"),
                ("planning", "overlay"),
            ),
        ),
        DocumentSignature(
            document_type=DocumentType.PROPERTY_REPORT,
            filename_terms=("property report", "detailed property report"),
            text_terms=(
                "detailed property report",
                "property information",
                "services connected",
                "council rates",
            ),
            required_term_groups=(
                ("property", "report"),
                ("services", "connected"),
            ),
        ),
    )

    @staticmethod
    def resolve_path(value: str | Path) -> Path:
        return Path(value).expanduser().resolve()


settings = Settings()
