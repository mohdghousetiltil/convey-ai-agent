"""Integration tests for the canonical vendor form extractor.

These tests run against a real vendor instruction form PDF
(`samples/Vendor Instruction Form-Kyle Sultana.pdf`) to guarantee that
the rule-based extractor produces the right Facts at the right paths,
with verbatim quotes attached.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from triconvey_agent.canonical.extractors.vendor_form import (
    EXTRACTOR_NAME,
    extract_vendor_form_facts,
)
from triconvey_agent.canonical.schemas import Fact
from triconvey_agent.schemas.documents import Document, InputFileType
from triconvey_agent.ingest.pdf_loader import load_pdf_document

SAMPLE_PDF = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "Vendor Instruction Form-Kyle Sultana.pdf"
)


def _by_path(facts: list[Fact]) -> dict[str, Fact]:
    """Index facts by their canonical path (last write wins — extractor only emits once)."""
    return {f.path: f for f in facts}


@unittest.skipUnless(SAMPLE_PDF.exists(), f"sample PDF missing: {SAMPLE_PDF}")
class TestVendorFormExtractorRealPDF(unittest.TestCase):
    """Single-PDF integration test — loads once, asserts many."""

    @classmethod
    def setUpClass(cls):
        cls.doc = load_pdf_document(SAMPLE_PDF)
        cls.facts = extract_vendor_form_facts(cls.doc)
        cls.by_path = _by_path(cls.facts)

    # ---- structural sanity ------------------------------------------------

    def test_extractor_returns_many_facts(self):
        self.assertGreater(len(self.facts), 30,
                           "expected >30 facts from a fully populated vendor form")

    def test_every_fact_uses_canonical_extractor_name(self):
        for f in self.facts:
            self.assertEqual(f.extractor, EXTRACTOR_NAME)
        self.assertEqual(EXTRACTOR_NAME, "rule:vendor_form_v2")

    def test_every_fact_has_verbatim_quote(self):
        for f in self.facts:
            self.assertEqual(len(f.sources), 1, f"{f.path} should have one source")
            src = f.sources[0]
            self.assertTrue(src.quote, f"{f.path} has empty quote")
            self.assertTrue(src.quote_verified, f"{f.path} quote not verified")
            self.assertEqual(src.file, "Vendor Instruction Form-Kyle Sultana.pdf")

    def test_no_duplicate_paths(self):
        paths = [f.path for f in self.facts]
        self.assertEqual(len(paths), len(set(paths)),
                         "extractor should emit at most one fact per path")

    # ---- multi-vendor identity --------------------------------------------

    def test_vendor_count_is_two(self):
        self.assertEqual(self.by_path["vendor.count"].value, 2)

    def test_vendor_zero_is_kyle_sultana(self):
        self.assertEqual(self.by_path["vendor.0.title"].value, "Mr")
        self.assertEqual(self.by_path["vendor.0.first_name"].value, "Kyle")
        self.assertEqual(self.by_path["vendor.0.last_name"].value, "Sultana")
        self.assertEqual(self.by_path["vendor.0.email"].value, "sultakyle13@gmail.com")
        self.assertEqual(self.by_path["vendor.0.phone"].value, "0414 186 695")
        self.assertEqual(self.by_path["vendor.0.nationality"].value, "Australian")

    def test_vendor_one_is_erin_sultana(self):
        self.assertEqual(self.by_path["vendor.1.title"].value, "Mrs")
        self.assertEqual(self.by_path["vendor.1.first_name"].value, "Erin")
        self.assertEqual(self.by_path["vendor.1.last_name"].value, "Sultana")
        self.assertEqual(self.by_path["vendor.1.email"].value, "Erinkselleck@gmail.com")

    def test_vendor_quotes_contain_their_label(self):
        kyle = self.by_path["vendor.0.first_name"]
        self.assertIn("Kyle", kyle.sources[0].quote)
        self.assertIn("Your first name", kyle.sources[0].quote)

    # ---- property ----------------------------------------------------------

    def test_property_address(self):
        self.assertEqual(
            self.by_path["property.address"].value,
            "43 Southampton Dr, Mulgrave VIC 3170, Australia",
        )

    def test_property_nature_residential(self):
        self.assertEqual(self.by_path["property.nature"].value, "Residential")

    def test_property_owner_occupied_true(self):
        self.assertIs(self.by_path["property.owner_occupied"].value, True)

    def test_property_leased_false(self):
        self.assertIs(self.by_path["property.leased"].value, False)

    def test_property_insured_true(self):
        self.assertIs(self.by_path["property.insured"].value, True)

    # ---- rates -------------------------------------------------------------

    def test_council_authority_and_amount(self):
        self.assertEqual(
            self.by_path["rates.council.authority_name"].value, "City of Monash"
        )
        self.assertEqual(
            self.by_path["rates.council.annual_amount"].value, "$1,198.25"
        )

    def test_water_authority_and_amount(self):
        self.assertEqual(
            self.by_path["rates.water.authority_name"].value, "Yarra Valley"
        )
        self.assertEqual(
            self.by_path["rates.water.annual_amount"].value, "$774.00"
        )

    def test_land_tax_not_payable(self):
        self.assertIs(self.by_path["rates.land_tax.payable"].value, False)

    def test_owners_corporation_false(self):
        self.assertIs(self.by_path["rates.owners_corporation.exists"].value, False)

    # ---- services connected (the bug from earlier sessions) ---------------

    def test_electricity_connected(self):
        self.assertIs(self.by_path["services.electricity.connected"].value, True)

    def test_gas_connected(self):
        self.assertIs(self.by_path["services.gas.connected"].value, True)

    def test_water_connected(self):
        self.assertIs(self.by_path["services.water.connected"].value, True)

    def test_sewerage_connected(self):
        self.assertIs(self.by_path["services.sewerage.connected"].value, True)

    def test_nbn_connected_via_voip(self):
        """Earlier sessions missed this — VOIP under NBN should mark NBN connected."""
        self.assertIs(self.by_path["services.nbn.connected"].value, True)

    def test_septic_and_tank_not_connected(self):
        self.assertIs(self.by_path["services.septic_tank.connected"].value, False)
        self.assertIs(self.by_path["services.tank_water.connected"].value, False)
        self.assertIs(self.by_path["services.bottled_gas.connected"].value, False)

    def test_service_facts_have_useful_quotes(self):
        elec = self.by_path["services.electricity.connected"]
        self.assertIn("Mains Electricity", elec.sources[0].quote)

    # ---- planning / building ----------------------------------------------

    def test_bushfire_prone_false(self):
        self.assertIs(self.by_path["planning.bushfire_prone"].value, False)

    def test_road_access_true(self):
        self.assertIs(self.by_path["property.road_access"].value, True)

    def test_no_building_permits_last_7_years(self):
        self.assertIs(self.by_path["building.permits_last_7_years"].value, False)

    def test_building_works_last_7_years_true(self):
        self.assertIs(self.by_path["building.works_last_7_years"].value, True)

    # ---- trust -------------------------------------------------------------

    def test_not_a_trust(self):
        self.assertIs(self.by_path["vendor.is_trust"].value, False)

    # ---- insurance disclosure ---------------------------------------------

    def test_insurance_text_captured(self):
        fact = self.by_path["insurance.home.vendor_disclosed_text"]
        text = fact.value
        self.assertIn("RACV", text)
        self.assertIn("HOM798171110", text)
        # Confidence is intentionally low — Certificate of Currency is authoritative
        self.assertLess(fact.confidence, 0.85)


@unittest.skipUnless(SAMPLE_PDF.exists(), f"sample PDF missing: {SAMPLE_PDF}")
class TestVendorFormExtractorGuards(unittest.TestCase):
    """Negative paths — extractor should refuse to fire on the wrong inputs."""

    def test_empty_document_returns_no_facts(self):
        from triconvey_agent.schemas.documents import (
            Document,
            InputFileType,
        )

        empty = Document(
            source_path=Path("nonexistent.pdf"),
            filename="nonexistent.pdf",
            file_type=InputFileType.PDF,
            raw_text="",
        )
        self.assertEqual(extract_vendor_form_facts(empty), [])

    def test_unrelated_document_returns_no_facts(self):
        from triconvey_agent.schemas.documents import (
            Document,
            InputFileType,
        )

        bogus = Document(
            source_path=Path("notes.pdf"),
            filename="notes.pdf",
            file_type=InputFileType.PDF,
            raw_text="this is just some random text without the services header",
        )
        self.assertEqual(extract_vendor_form_facts(bogus), [])

class TestVendorFormExtractorServiceNegatives(unittest.TestCase):
    def test_landline_not_connected_but_available_is_treated_as_not_connected(self):
        doc = Document(
            source_path=Path("vendor.pdf"),
            filename="vendor.pdf",
            file_type=InputFileType.PDF,
            raw_text=(
                "Services listed connected to the land\n"
                "Land Line Telephone not connected but available\n"
                "NBN\n"
                "Do you live at the property?\n"
                "Yes\n"
            ),
        )
        facts = extract_vendor_form_facts(doc)
        by_path = _by_path(facts)
        self.assertIs(by_path["services.telephone.connected"].value, False)
        self.assertIs(by_path["services.nbn.connected"].value, True)


if __name__ == "__main__":
    unittest.main()
