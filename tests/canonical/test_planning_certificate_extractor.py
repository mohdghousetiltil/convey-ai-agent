from __future__ import annotations

import unittest
from pathlib import Path

from triconvey_agent.canonical.extractors.planning_certificate import extract_planning_certificate_facts
from triconvey_agent.schemas.documents import Document, DocumentPage, InputFileType


def _make_doc(text: str, *, pages: list[str] | None = None) -> Document:
    return Document(
        source_path=Path("planning.pdf"),
        filename="planning.pdf",
        file_type=InputFileType.PDF,
        raw_text=text,
        pages=[
            DocumentPage(page_number=index + 1, text=page_text, normalized_text=page_text)
            for index, page_text in enumerate(pages or [])
        ],
    )


class TestPlanningCertificateBushfireExtraction(unittest.TestCase):
    def test_explicit_negative_statement_wins(self):
        doc = _make_doc(
            "PLANNING PROPERTY REPORT\nThis property is not in a designated bushfire prone area.",
            pages=[
                "Header\nThis property is not in a designated bushfire prone area.\nFooter",
                "Header\nLegend mentioning Bushfire Management Overlay (BMO)\nFooter",
            ],
        )

        facts = extract_planning_certificate_facts(doc)
        bushfire = [fact for fact in facts if fact.path == "planning.bushfire_prone"]

        self.assertEqual(len(bushfire), 1)
        self.assertIs(bushfire[0].value, False)
        self.assertIn("not in a designated bushfire prone area", bushfire[0].sources[0].quote.lower())

    def test_bare_bmo_legend_does_not_create_positive(self):
        doc = _make_doc(
            "PLANNING PROPERTY REPORT\nLegend: Bushfire Management Overlay (BMO)",
            pages=["Header\nLegend: Bushfire Management Overlay (BMO)\nFooter"],
        )

        facts = extract_planning_certificate_facts(doc)
        bushfire = [fact for fact in facts if fact.path == "planning.bushfire_prone"]

        self.assertEqual(bushfire, [])

    def test_footer_disclaimer_does_not_create_positive(self):
        doc = _make_doc(
            "PLANNING PROPERTY REPORT",
            pages=[
                "Designated Bushfire Prone Areas\nDesignated Bushfire Prone Areas\nThis property is not in a designated bushfire prone area.\nNo special bushfire construction requirements apply.\nNotwithstanding this disclaimer, a vendor may rely on the information in this report for the purpose of a statement that land is in a bushfire prone area as required by section 32C (b) of the Sale of Land Act 1962 (Vic)."
            ],
        )

        facts = extract_planning_certificate_facts(doc)
        bushfire = [fact for fact in facts if fact.path == "planning.bushfire_prone"]

        self.assertEqual(len(bushfire), 1)
        self.assertIs(bushfire[0].value, False)


if __name__ == "__main__":
    unittest.main()
