from __future__ import annotations

from triconvey_agent.canonical.extractors.owners_corporation import extract_owners_corporation_facts
from pathlib import Path

from triconvey_agent.schemas.documents import Document, DocumentPage, DocumentType, InputFileType


def _doc(text: str, filename: str = "Owners Corporation Certificate (combined).pdf") -> Document:
    return Document(
        source_path=Path(filename),
        filename=filename,
        file_type=InputFileType.PDF,
        document_type=DocumentType.UNKNOWN,
        raw_text=text,
        normalized_text=text,
        pages=[DocumentPage(page_number=1, text=text, normalized_text=text)],
    )


def test_extracts_payable_annually_oc_amount() -> None:
    text = (
        "OWNERS CORPORATION CERTIFICATE\n"
        "Owners Corporation No PS502358\n"
        "The current fees for insurance of the common property are $425.00 payable annually.\n"
    )

    facts = extract_owners_corporation_facts(_doc(text))
    values = {fact.path: fact.value for fact in facts}

    assert values["rates.owners_corporation.exists"] is True
    assert "Owners Corporation" in str(values["rates.owners_corporation.authority_name"])
    assert values["rates.owners_corporation.annual_amount"] == "$425.00"
