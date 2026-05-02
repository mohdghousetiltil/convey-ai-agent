from pathlib import Path

from pypdf import PdfReader

from triconvey_agent.backend.triconvey_import_utils import collect_water_rows_from_facts
from triconvey_agent.canonical.extractors.water_authority_certificate import (
    extract_water_authority_certificate_facts,
)
from triconvey_agent.schemas.documents import Document, DocumentPage, DocumentType, InputFileType


def _load_pdf_doc(path_str: str) -> Document:
    path = Path(path_str)
    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    return Document(
        source_path=path,
        filename=path.name,
        file_type=InputFileType.PDF,
        document_type=DocumentType.UNKNOWN,
        raw_text=text,
        normalized_text=text,
        pages=[DocumentPage(page_number=1, text=text, normalized_text=text)],
    )


def _fact_to_dict(fact) -> dict:
    return fact.model_dump(mode="json")


def test_gippsland_water_extracts_its_own_annual_amount_from_uploaded_pdf() -> None:
    doc = _load_pdf_doc(
        r"C:/Users/moham/Downloads/VIC_ Enquiry - Gippsland Water_ Water Information and Special Meter Reading - 9045_910, 8988_215.pdf"
    )
    facts = extract_water_authority_certificate_facts(doc)
    by_path = {}
    for fact in facts:
        by_path.setdefault(fact.path, []).append(fact)

    annual = by_path["rates.water.annual_amount"][0]
    authority = by_path["rates.water.authority_name"][0]

    assert authority.value == "Gippsland Water"
    assert annual.value == "$194.07"
    assert "fixed service charge" in (annual.notes or "").lower()


def test_two_water_documents_produce_two_separate_water_rows() -> None:
    se_doc = _load_pdf_doc(
        r"C:/Users/moham/Downloads/VIC_ Enquiry - South East Water_ Water Information Statement - 9045_910, 8988_215.pdf"
    )
    gipps_doc = _load_pdf_doc(
        r"C:/Users/moham/Downloads/VIC_ Enquiry - Gippsland Water_ Water Information and Special Meter Reading - 9045_910, 8988_215.pdf"
    )

    facts = extract_water_authority_certificate_facts(se_doc) + extract_water_authority_certificate_facts(gipps_doc)
    facts_by_path: dict[str, list[dict]] = {}
    for fact in facts:
        facts_by_path.setdefault(fact.path, []).append(_fact_to_dict(fact))

    rows = collect_water_rows_from_facts(facts_by_path, rules=[])

    assert {"authority": "South East Water", "amount": "$68.60"} in rows
    assert {"authority": "Gippsland Water", "amount": "$194.07"} in rows
    assert len(rows) == 2
