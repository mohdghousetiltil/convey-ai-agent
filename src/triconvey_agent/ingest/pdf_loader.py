from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from triconvey_agent.config import DocumentSignature, settings
from triconvey_agent.ingest.normalizer import (
    collapse_snippet,
    contains_all_terms,
    filename_key,
    normalize_text,
    to_search_blob,
)
from triconvey_agent.schemas.documents import (
    Document,
    DocumentPage,
    DocumentType,
    InputFileType,
)


def _score_signature(
    signature: DocumentSignature,
    *,
    filename_blob: str,
    text_blob: str,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    for term in signature.filename_terms:
        if term in filename_blob:
            score += settings.filename_term_weight
            reasons.append(f"filename matched '{term}'")

    for term in signature.text_terms:
        if term in text_blob:
            score += settings.text_term_weight
            reasons.append(f"text matched '{term}'")

    for terms in signature.required_term_groups:
        if contains_all_terms(text_blob, terms):
            score += settings.required_group_weight
            reasons.append(f"text contained grouped terms {terms}")

    return score, reasons


def classify_pdf_document(filename: str, extracted_text: str) -> tuple[DocumentType, float, list[str]]:
    filename_blob = filename_key(filename)
    text_blob = to_search_blob(extracted_text[:12000])

    best_type = DocumentType.UNKNOWN
    best_score = 0.0
    best_reasons: list[str] = []
    second_best = 0.0

    for signature in settings.document_signatures:
        score, reasons = _score_signature(
            signature,
            filename_blob=filename_blob,
            text_blob=text_blob,
        )
        if score > best_score:
            second_best = best_score
            best_type = signature.document_type
            best_score = score
            best_reasons = reasons
        elif score > second_best:
            second_best = score

    if best_type == DocumentType.UNKNOWN:
        return DocumentType.UNKNOWN, 0.0, []

    # SEC32_TAB is a TriConvey YAML form — it should NEVER match a PDF file.
    # The signature contains terms like "land tax" that appear in government certificates,
    # causing false positives.  Hard-block it here.
    if best_type == DocumentType.SEC32_TAB:
        return DocumentType.UNKNOWN, 0.0, []

    signature = next(item for item in settings.document_signatures if item.document_type == best_type)
    if best_score < signature.minimum_score:
        return DocumentType.UNKNOWN, 0.0, []

    confidence = min(0.99, 0.4 + (best_score / 10.0))
    if second_best > 0:
        confidence = max(0.0, confidence - min(0.25, (second_best / best_score) * settings.ambiguity_penalty))

    return best_type, round(confidence, 3), best_reasons


def load_pdf_document(path: str | Path) -> Document:
    file_path = Path(path).expanduser().resolve()
    reader = PdfReader(str(file_path))

    pages: list[DocumentPage] = []
    raw_page_texts: list[str] = []

    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        raw_page_texts.append(page_text.strip())
        pages.append(
            DocumentPage(
                page_number=page_number,
                text=page_text,
                normalized_text=normalize_text(page_text),
                metadata={},
            )
        )

    raw_text = "\n\n".join(part for part in raw_page_texts if part).strip()
    normalized_text = normalize_text(raw_text)
    document_type, confidence, reasons = classify_pdf_document(file_path.name, normalized_text)
    source_subtype = None
    lowered_filename = file_path.name.lower()
    lowered_text = normalized_text.lower()
    if document_type == DocumentType.VIC_TITLE and "register search statement" in lowered_text:
        source_subtype = "register_search_statement"
    elif document_type == DocumentType.PLANNING_REPORT and "vicplan" in lowered_filename:
        source_subtype = "vicplan_planning_property_report"
    elif document_type == DocumentType.PROPERTY_REPORT and "detailed property report" in lowered_filename:
        source_subtype = "detailed_property_report"
    elif document_type == DocumentType.VENDOR_FORM and "vendor instruction form" in lowered_text:
        source_subtype = "vendor_instruction_form"

    return Document(
        source_path=file_path,
        filename=file_path.name,
        file_type=InputFileType.PDF,
        document_type=document_type,
        classification_confidence=confidence,
        classification_reasons=reasons,
        raw_text=raw_text,
        normalized_text=normalized_text,
        pages=pages,
        metadata={
            "loader": "pdf_loader",
            "file_size_bytes": file_path.stat().st_size,
            "source_subtype": source_subtype,
            "page_count": len(pages),
            "text_preview": collapse_snippet(normalized_text),
        },
    )
