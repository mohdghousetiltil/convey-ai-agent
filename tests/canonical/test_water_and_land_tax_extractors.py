"""Integration tests for the water-authority and land-tax extractors,
plus an end-to-end check that authority resolution prefers them over
the vendor form on overlapping rates / property paths.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from triconvey_agent.canonical.extractors.land_tax_certificate import (
    EXTRACTOR_NAME as LAND_TAX_EXTRACTOR_NAME,
    extract_land_tax_certificate_facts,
)
from triconvey_agent.canonical.extractors.vendor_form import (
    extract_vendor_form_facts,
)
from triconvey_agent.canonical.extractors.water_authority_certificate import (
    EXTRACTOR_NAME as WATER_AUTHORITY_EXTRACTOR_NAME,
    extract_water_authority_certificate_facts,
)
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.schemas import Fact
from triconvey_agent.ingest.pdf_loader import load_pdf_document

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
WATER_PDF = SAMPLES / "VIC_ Enquiry - Yarra Valley Water_ Water Information Statement.pdf"
LAND_TAX_PDF = SAMPLES / (
    "VIC_ Enquiry - State Revenue Office_ Land Tax Certificate "
    "- Unknown - 43 SOUTHAMPTON DRIVE, MULGRAVE VIC 3170.pdf"
)
VENDOR_PDF = SAMPLES / "Vendor Instruction Form-Kyle Sultana.pdf"


def _by_path(facts: list[Fact]) -> dict[str, Fact]:
    return {f.path: f for f in facts}


@unittest.skipUnless(WATER_PDF.exists(), f"missing: {WATER_PDF}")
class TestWaterAuthorityExtractor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load_pdf_document(WATER_PDF)
        cls.facts = extract_water_authority_certificate_facts(cls.doc)
        cls.by_path = _by_path(cls.facts)

    def test_returns_facts(self):
        self.assertGreater(len(self.facts), 15)

    def test_extractor_name(self):
        self.assertEqual(WATER_AUTHORITY_EXTRACTOR_NAME, "rule:water_authority_certificate_v1")
        for f in self.facts:
            self.assertEqual(f.extractor, WATER_AUTHORITY_EXTRACTOR_NAME)

    def test_authority_name(self):
        self.assertEqual(self.by_path["rates.water.authority_name"].value, "Yarra Valley Water")

    def test_certificate_number(self):
        self.assertEqual(self.by_path["rates.water.certificate_number"].value, "31024470")

    def test_property_row(self):
        self.assertEqual(
            self.by_path["property.address"].value,
            "43 SOUTHAMPTON DR, MULGRAVE VIC 3170",
        )
        self.assertEqual(self.by_path["property.lot_number"].value, "1620")
        self.assertEqual(self.by_path["property.plan_number"].value, "PS826454")
        self.assertEqual(self.by_path["rates.water.account_number"].value, "5239288")
        self.assertEqual(self.by_path["rates.water.property_type"].value, "Residential")

    def test_total_outstanding(self):
        self.assertEqual(self.by_path["rates.water.total_outstanding"].value, "$195.85")
        # The annual_amount fact is also written for the FactStore (lower confidence).
        self.assertEqual(self.by_path["rates.water.annual_amount"].value, "$195.85")

    def test_charge_lines(self):
        self.assertGreaterEqual(self.by_path["rates.water.charges.count"].value, 4)
        self.assertEqual(
            self.by_path["rates.water.charges.0.label"].value,
            "Residential Water Service Charge",
        )
        self.assertEqual(self.by_path["rates.water.charges.0.amount"].value, "$21.04")

    def test_period(self):
        self.assertEqual(self.by_path["rates.water.period_from"].value, "01-04-2026")
        self.assertEqual(self.by_path["rates.water.period_to"].value, "30-06-2026")

    def test_implies_water_and_sewerage_connected(self):
        self.assertIs(self.by_path["services.water.connected"].value, True)
        self.assertIs(self.by_path["services.sewerage.connected"].value, True)


@unittest.skipUnless(LAND_TAX_PDF.exists(), f"missing: {LAND_TAX_PDF}")
class TestLandTaxExtractor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load_pdf_document(LAND_TAX_PDF)
        cls.facts = extract_land_tax_certificate_facts(cls.doc)
        cls.by_path = _by_path(cls.facts)

    def test_returns_facts(self):
        self.assertGreater(len(self.facts), 12)

    def test_extractor_name(self):
        self.assertEqual(LAND_TAX_EXTRACTOR_NAME, "rule:land_tax_certificate_v1")

    def test_certificate_number(self):
        self.assertEqual(self.by_path["rates.land_tax.certificate_number"].value, "98550677")

    def test_address_lot_plan_volume_folio(self):
        self.assertEqual(
            self.by_path["property.address"].value,
            "43 SOUTHAMPTON DRIVE MULGRAVE VIC 3170",
        )
        self.assertEqual(self.by_path["property.lot_number"].value, "1620")
        self.assertEqual(self.by_path["property.plan_number"].value, "826454")
        self.assertEqual(self.by_path["property.volume_or_book"].value, "12287")
        self.assertEqual(self.by_path["property.folio_or_number"].value, "279")

    def test_tax_payable_zero_means_payable_false(self):
        self.assertEqual(self.by_path["rates.land_tax.amount"].value, "$0.00")
        self.assertIs(self.by_path["rates.land_tax.payable"].value, False)
        self.assertIs(self.by_path["rates.land_tax.vacant_residential_payable"].value, False)

    def test_valuations(self):
        self.assertEqual(self.by_path["rates.land_tax.taxable_value"].value, "$220,000")
        self.assertEqual(self.by_path["rates.land_tax.site_value"].value, "$220,000")
        self.assertEqual(self.by_path["rates.land_tax.capital_improved_value"].value, "$615,000")

    def test_exemption_reason(self):
        self.assertIn(
            "Principal Place of Residence",
            self.by_path["rates.land_tax.exemption_reason"].value,
        )

    def test_assessed_year_and_commissioner(self):
        self.assertEqual(self.by_path["rates.land_tax.assessed_year"].value, "2026")
        self.assertEqual(self.by_path["rates.land_tax.commissioner"].value, "Paul Broderick")

    def test_vendor_name_present(self):
        self.assertIn("ERIN", self.by_path["rates.land_tax.vendor_name"].value)


@unittest.skipUnless(
    WATER_PDF.exists() and LAND_TAX_PDF.exists() and VENDOR_PDF.exists(),
    "missing one or more sample PDFs",
)
class TestAuthorityResolutionAcrossSources(unittest.TestCase):
    """All four extractors firing into one FactStore — verify the authority
    rules pick the right winner on every overlapping path."""

    @classmethod
    def setUpClass(cls):
        store = FactStoreImpl()
        store.add_many(extract_vendor_form_facts(load_pdf_document(VENDOR_PDF)))
        store.add_many(extract_water_authority_certificate_facts(load_pdf_document(WATER_PDF)))
        store.add_many(extract_land_tax_certificate_facts(load_pdf_document(LAND_TAX_PDF)))
        cls.store = store

    def test_water_authority_wins_for_water_rates(self):
        winner, _ = self.store.get("rates.water.authority_name")
        self.assertEqual(winner.value, "Yarra Valley Water")
        self.assertEqual(winner.extractor, "rule:water_authority_certificate_v1")

    def test_land_tax_cert_wins_for_land_tax_payable(self):
        winner, _ = self.store.get("rates.land_tax.payable")
        self.assertIs(winner.value, False)
        self.assertEqual(winner.extractor, "rule:land_tax_certificate_v1")

    def test_water_authority_wins_for_services_water_connected(self):
        """Per the services.water.* rule, the water authority cert outranks
        the vendor form."""
        winner, _ = self.store.get("services.water.connected")
        self.assertIs(winner.value, True)
        self.assertEqual(winner.extractor, "rule:water_authority_certificate_v1")

    def test_water_authority_wins_for_services_sewerage_connected(self):
        winner, _ = self.store.get("services.sewerage.connected")
        self.assertIs(winner.value, True)
        self.assertEqual(winner.extractor, "rule:water_authority_certificate_v1")


if __name__ == "__main__":
    unittest.main()
