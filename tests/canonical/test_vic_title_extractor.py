"""Integration tests for the canonical Victorian title extractor.

Runs against `samples/VIC_ Certificate - Register Search Statement (Copy
of Title) Volume 12287 Folio 279.pdf` and asserts that every fact we
care about is extracted with the correct path, value, and verbatim
quote.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from triconvey_agent.canonical.extractors.vic_title import (
    EXTRACTOR_NAME,
    extract_vic_title_facts,
)
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.schemas import Fact
from triconvey_agent.ingest.pdf_loader import load_pdf_document

SAMPLE_PDF = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "VIC_ Certificate - Register Search Statement (Copy of Title) Volume 12287 Folio 279.pdf"
)


def _by_path(facts: list[Fact]) -> dict[str, Fact]:
    return {f.path: f for f in facts}


@unittest.skipUnless(SAMPLE_PDF.exists(), f"sample PDF missing: {SAMPLE_PDF}")
class TestVicTitleExtractorRealPDF(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.doc = load_pdf_document(SAMPLE_PDF)
        cls.facts = extract_vic_title_facts(cls.doc)
        cls.by_path = _by_path(cls.facts)

    # ---- structural ------------------------------------------------------

    def test_returns_many_facts(self):
        self.assertGreater(len(self.facts), 30)

    def test_extractor_name(self):
        self.assertEqual(EXTRACTOR_NAME, "rule:vic_title_v1")
        for f in self.facts:
            self.assertEqual(f.extractor, EXTRACTOR_NAME)

    def test_every_fact_has_quote(self):
        for f in self.facts:
            self.assertEqual(len(f.sources), 1)
            self.assertTrue(f.sources[0].quote)
            self.assertTrue(f.sources[0].quote_verified)

    def test_no_duplicate_paths(self):
        paths = [f.path for f in self.facts]
        self.assertEqual(len(paths), len(set(paths)))

    # ---- volume / folio --------------------------------------------------

    def test_volume_folio(self):
        self.assertEqual(self.by_path["title.volume_folio"].value, "VOLUME 12287 FOLIO 279")
        self.assertEqual(self.by_path["title.volume"].value, "12287")
        self.assertEqual(self.by_path["title.folio"].value, "279")

    def test_volume_folio_also_written_to_property_paths(self):
        self.assertEqual(self.by_path["property.volume_or_book"].value, "12287")
        self.assertEqual(self.by_path["property.folio_or_number"].value, "279")

    # ---- header meta -----------------------------------------------------

    def test_security_number(self):
        self.assertEqual(self.by_path["title.security_number"].value, "124133522109C")

    def test_produced_at(self):
        self.assertEqual(self.by_path["title.produced_at"].value, "02/04/2026 06:20 PM")

    # ---- land description / plan / lot -----------------------------------

    def test_land_description(self):
        self.assertEqual(
            self.by_path["title.land_description"].value,
            "Lot 1620 on Plan of Subdivision 826454D.",
        )

    def test_plan_type_and_number(self):
        self.assertEqual(self.by_path["title.plan_type"].value, "Plan of Subdivision")
        self.assertEqual(self.by_path["title.plan_number"].value, "826454D")
        self.assertEqual(self.by_path["property.plan_number"].value, "826454D")

    def test_lot_number(self):
        self.assertEqual(self.by_path["title.lot_number"].value, "1620")
        self.assertEqual(self.by_path["property.lot_number"].value, "1620")

    # ---- parent + creation -----------------------------------------------

    def test_parent_title(self):
        self.assertEqual(self.by_path["title.parent_title"].value, "Volume 12202 Folio 017")
        self.assertEqual(self.by_path["title.parent_volume"].value, "12202")
        self.assertEqual(self.by_path["title.parent_folio"].value, "017")

    def test_created_by(self):
        self.assertEqual(self.by_path["title.created_by_instrument"].value, "PS826454D")
        self.assertEqual(self.by_path["title.created_at"].value, "23/02/2021")

    # ---- proprietor ------------------------------------------------------

    def test_estate_and_tenancy(self):
        self.assertEqual(self.by_path["title.estate"].value, "Fee Simple")
        self.assertEqual(self.by_path["title.tenancy"].value, "Sole Proprietor")

    def test_proprietor_count_and_details(self):
        self.assertEqual(self.by_path["title.proprietor.count"].value, 1)
        self.assertEqual(
            self.by_path["title.proprietor.0.name"].value, "ERIN KRISTINE SELLECK"
        )
        self.assertEqual(
            self.by_path["title.proprietor.0.address"].value,
            "43 SOUTHAMPTON DRIVE MULGRAVE VIC 3170",
        )
        self.assertEqual(self.by_path["title.proprietor.0.dealing"].value, "AU146757D")
        self.assertEqual(
            self.by_path["title.proprietor.0.dealing_date"].value, "17/03/2021"
        )

    # ---- encumbrances ----------------------------------------------------

    def test_encumbrance_count_is_three(self):
        """MORTGAGE + COVENANT + NOTICE — the inline 'DIAGRAM LOCATION' phrase
        inside the covenant body must NOT terminate the section early."""
        self.assertEqual(self.by_path["title.encumbrance.count"].value, 3)

    def test_mortgage_entry(self):
        self.assertEqual(self.by_path["title.encumbrance.0.type"].value, "MORTGAGE")
        self.assertEqual(self.by_path["title.encumbrance.0.number"].value, "BA088550M")
        self.assertEqual(self.by_path["title.encumbrance.0.date"].value, "04/02/2026")
        self.assertIn(
            "AUSTRALIA AND NEW ZEALAND BANKING GROUP LTD",
            self.by_path["title.encumbrance.0.party"].value,
        )

    def test_covenant_entry(self):
        self.assertEqual(self.by_path["title.encumbrance.1.type"].value, "COVENANT")
        self.assertEqual(self.by_path["title.encumbrance.1.number"].value, "PS826454D")
        self.assertEqual(self.by_path["title.encumbrance.1.date"].value, "23/02/2021")

    def test_notice_entry_present_and_clean(self):
        """Heritage Act notice — must be detected, and the dealing-number
        slot must NOT be polluted by the word 'Section'."""
        self.assertEqual(self.by_path["title.encumbrance.2.type"].value, "NOTICE")
        # The NOTICE head has no dealing number, so the path either is
        # absent or is a real dealing pattern. It must not be 'S' or
        # 'Section'.
        if "title.encumbrance.2.number" in self.by_path:
            number = self.by_path["title.encumbrance.2.number"].value
            self.assertNotEqual(number, "S")
            self.assertNotIn("Section", number)
        # The Heritage Act mention must show up in the body text.
        self.assertIn("Heritage Act", self.by_path["title.encumbrance.2.text"].value)

    def test_has_flags(self):
        self.assertIs(self.by_path["title.has_mortgage"].value, True)
        self.assertIs(self.by_path["title.has_covenant"].value, True)
        self.assertIs(self.by_path["title.has_caveat"].value, False)
        self.assertIs(self.by_path["title.has_notice"].value, True)

    # ---- diagram location / addresses / ect ------------------------------

    def test_diagram_location_does_not_eat_notice_block(self):
        """The diagram_location should be the post-encumbrances paragraph,
        not the encumbrances themselves."""
        value = self.by_path["title.diagram_location"].value
        self.assertIn("PS826454D", value)
        self.assertNotIn("Heritage Act", value)

    def test_street_address(self):
        self.assertEqual(
            self.by_path["title.street_address"].value,
            "43 SOUTHAMPTON DRIVE MULGRAVE VIC 3170",
        )
        self.assertEqual(
            self.by_path["property.address"].value,
            "43 SOUTHAMPTON DRIVE MULGRAVE VIC 3170",
        )

    def test_ect_control(self):
        self.assertIn(
            "AUSTRALIA AND NEW ZEALAND BANKING GROUP LIMITED",
            self.by_path["title.ect_controller"].value,
        )
        self.assertEqual(
            self.by_path["title.ect_effective_from"].value, "04/02/2026"
        )


@unittest.skipUnless(SAMPLE_PDF.exists(), f"sample PDF missing: {SAMPLE_PDF}")
class TestVicTitleAuthorityIntegration(unittest.TestCase):
    """End-to-end check: vic_title outranks vendor_form on overlapping paths."""

    def test_vic_title_outranks_vendor_form_on_property_paths(self):
        """When both extractors emit `property.lot_number`, the title-derived
        fact must win via the property.* authority rule."""
        from triconvey_agent.canonical.extractors.vendor_form import (
            extract_vendor_form_facts,
        )

        title_doc = load_pdf_document(SAMPLE_PDF)
        vendor_doc = load_pdf_document(
            Path(__file__).resolve().parents[2]
            / "samples"
            / "Vendor Instruction Form-Kyle Sultana.pdf"
        )

        store = FactStoreImpl()
        store.add_many(extract_vendor_form_facts(vendor_doc))
        store.add_many(extract_vic_title_facts(title_doc))

        winner, conflict = store.get("property.lot_number")
        self.assertIsNotNone(winner)
        self.assertEqual(winner.value, "1620")
        self.assertEqual(winner.extractor, "rule:vic_title_v1")
        # A conflict record exists because both extractors fired
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict.resolution, "authority_wins")

    def test_vic_title_provides_volume_folio_when_vendor_form_omits(self):
        """Vendor form left these blank — title should be the only source."""
        from triconvey_agent.canonical.extractors.vendor_form import (
            extract_vendor_form_facts,
        )

        title_doc = load_pdf_document(SAMPLE_PDF)
        vendor_doc = load_pdf_document(
            Path(__file__).resolve().parents[2]
            / "samples"
            / "Vendor Instruction Form-Kyle Sultana.pdf"
        )
        store = FactStoreImpl()
        store.add_many(extract_vendor_form_facts(vendor_doc))
        store.add_many(extract_vic_title_facts(title_doc))

        self.assertEqual(store.get_value("property.volume_or_book"), "12287")
        self.assertEqual(store.get_value("property.folio_or_number"), "279")


class TestVicTitleGuards(unittest.TestCase):
    def test_empty_document_returns_no_facts(self):
        from triconvey_agent.schemas.documents import Document, InputFileType

        empty = Document(
            source_path=Path("none.pdf"),
            filename="none.pdf",
            file_type=InputFileType.PDF,
            raw_text="",
        )
        self.assertEqual(extract_vic_title_facts(empty), [])

    def test_unrelated_document_returns_no_facts(self):
        from triconvey_agent.schemas.documents import Document, InputFileType

        bogus = Document(
            source_path=Path("notes.pdf"),
            filename="notes.pdf",
            file_type=InputFileType.PDF,
            raw_text="just some text without the magic phrase",
        )
        self.assertEqual(extract_vic_title_facts(bogus), [])


class TestVicTitleAgreementParsing(unittest.TestCase):
    def test_section_173_agreement_number_and_date_can_come_from_body_line(self):
        from triconvey_agent.schemas.documents import Document, InputFileType

        doc = Document(
            source_path=Path("title.pdf"),
            filename="title.pdf",
            file_type=InputFileType.PDF,
            raw_text=(
                "REGISTER SEARCH STATEMENT\n"
                "ENCUMBRANCES, CAVEATS AND NOTICES\n"
                "AGREEMENT  Section 173 Planning and Environment Act 1987\n"
                "    AL543380Q 08/12/2014\n"
                "DIAGRAM LOCATION\n"
                "SEE PLAN\n"
                "ACTIVITY IN THE LAST 125 DAYS\n"
            ),
        )
        facts = extract_vic_title_facts(doc)
        by_path = _by_path(facts)
        self.assertEqual(by_path["title.encumbrance.0.type"].value, "AGREEMENT")
        self.assertEqual(by_path["title.encumbrance.0.number"].value, "AL543380Q")
        self.assertEqual(by_path["title.encumbrance.0.date"].value, "08/12/2014")


if __name__ == "__main__":
    unittest.main()
