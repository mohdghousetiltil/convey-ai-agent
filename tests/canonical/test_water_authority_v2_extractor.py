"""Tests for water_authority_certificate_v2 extractor.

Covers:
- Property-block scoping (unit vs master/related blocks)
- Multi-line address parsing
- Daily-rate annualisation
- Subtotal exclusion
"""
from __future__ import annotations

import unittest
from pathlib import Path

from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
    extract_water_authority_certificate_facts_v2,
    extract_property_blocks,
)
from triconvey_agent.ingest.pdf_loader import load_pdf_document

SAMPLES = Path(__file__).resolve().parents[2] / "samples"

# ---------------------------------------------------------------------------
# Property-block scoping — unit with master block on same statement
# ---------------------------------------------------------------------------

UNIT_YARRA_PDF = SAMPLES / "Unit 1 - Yarra Valley Water.pdf"


@unittest.skipUnless(UNIT_YARRA_PDF.exists(), f"missing: {UNIT_YARRA_PDF}")
class TestExcludesRelatedMasterBlock(unittest.TestCase):
    """Annual amount must come only from the primary (unit) block,
    not from the master property block on the same statement."""

    @classmethod
    def setUpClass(cls):
        cls.doc = load_pdf_document(UNIT_YARRA_PDF)
        cls.result = extract_water_authority_certificate_facts_v2(cls.doc)

    def test_annual_amount(self):
        self.assertAlmostEqual(self.result["annual_amount"], 189.36, places=2)

    def test_breakdown_contains_drainage_fee(self):
        labels = [r["label"] for r in self.result.get("breakdown", [])]
        self.assertIn("Drainage Fee", labels)

    def test_breakdown_excludes_commercial_water_service_charge(self):
        labels = [r["label"] for r in self.result.get("breakdown", [])]
        self.assertNotIn("Commercial Water Service Charge", labels)

    def test_breakdown_excludes_commercial_sewer_service_charge(self):
        labels = [r["label"] for r in self.result.get("breakdown", [])]
        self.assertNotIn("Commercial Sewer Service Charge", labels)

    def test_primary_block_detected(self):
        self.assertIsNotNone(self.result.get("property_block"))

    def test_related_blocks_detected(self):
        excluded = self.result.get("excluded_property_blocks", [])
        self.assertGreater(len(excluded), 0)


# ---------------------------------------------------------------------------
# Unit tests for extract_property_blocks() — multi-line address handling
# ---------------------------------------------------------------------------


class TestExtractPropertyBlocksMultiLineAddress(unittest.TestCase):
    """extract_property_blocks() must parse addresses that wrap onto 2 lines."""

    SAMPLE_TEXT = (
        "Property Address                    Lot & Plan      Property Number  Property Type\n"
        "Unit 1\n"
        "14 Bell Street, Fitzroy             1/LP12345       1234567          Residential\n"
        "Agreement Type     Period              Charges    Outstanding\n"
        "Some Agreement     01/07/2025 - 30/09/2025   $52.50   $0.00\n"
        "Drainage Fee                                      $47.34\n"
        "\n"
        "Property Address                    Lot & Plan      Property Number  Property Type\n"
        "14 Bell Street, Fitzroy             0/LP12345       7654321          Commercial\n"
        "Agreement Type     Period              Charges    Outstanding\n"
        "Some Agreement     01/07/2025 - 30/09/2025   $78.89   $0.00\n"
        "Commercial Water Service Charge                   $78.89\n"
    )

    def test_finds_two_blocks(self):
        blocks = extract_property_blocks(self.SAMPLE_TEXT)
        self.assertEqual(len(blocks), 2)

    def test_first_block_is_primary(self):
        blocks = extract_property_blocks(self.SAMPLE_TEXT)
        primary = [b for b in blocks if b.is_primary]
        self.assertEqual(len(primary), 1)

    def test_second_block_is_related(self):
        blocks = extract_property_blocks(self.SAMPLE_TEXT)
        related = [b for b in blocks if b.is_related]
        self.assertEqual(len(related), 1)

    def test_lot_plan_parsed_from_wrapped_address(self):
        blocks = extract_property_blocks(self.SAMPLE_TEXT)
        primary = next(b for b in blocks if b.is_primary)
        self.assertIsNotNone(primary.lot_plan)


# ---------------------------------------------------------------------------
# Unit tests for subtotal exclusion
# ---------------------------------------------------------------------------


class TestSubtotalExclusion(unittest.TestCase):
    """_looks_like_summary_row() must reject subtotals from annual amount."""

    def test_subtotal_not_in_breakdown(self):
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            RawRow,
            ClassifiedRow,
            RowType,
            _looks_like_summary_row,
        )

        rows = [
            ClassifiedRow(raw=RawRow("Service Charge A", 22.45, 91, source="period_line"), row_type=RowType.recurring_charge, annualised=89.80),
            ClassifiedRow(raw=RawRow("Service Charge B", 31.25, 91, source="period_line"), row_type=RowType.recurring_charge, annualised=125.00),
            ClassifiedRow(raw=RawRow("Subtotal Service Charges", 53.70, 91, source="period_line"), row_type=RowType.recurring_charge, annualised=214.80),
        ]
        subtotal_row = rows[2]
        self.assertTrue(_looks_like_summary_row(subtotal_row, rows))

    def test_non_subtotal_not_excluded(self):
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            RawRow,
            ClassifiedRow,
            RowType,
            _looks_like_summary_row,
        )

        rows = [
            ClassifiedRow(raw=RawRow("Service Charge A", 22.45, 91, source="period_line"), row_type=RowType.recurring_charge, annualised=89.80),
            ClassifiedRow(raw=RawRow("Service Charge B", 31.25, 91, source="period_line"), row_type=RowType.recurring_charge, annualised=125.00),
            ClassifiedRow(raw=RawRow("Drainage Fee", 18.00, 91, source="period_line"), row_type=RowType.recurring_charge, annualised=72.00),
        ]
        drainage_row = rows[2]
        self.assertFalse(_looks_like_summary_row(drainage_row, rows))


# ---------------------------------------------------------------------------
# Gippsland stacked layout
# ---------------------------------------------------------------------------


class TestGippslandStackedParser(unittest.TestCase):
    """_parse_gippsland_stacked() and end-to-end annual amount for Gippsland docs."""

    SAMPLE_TEXT = (
        "Gippsland Water\n"
        "Water Information Statement\n"
        "Gippsland Water billing periods: 3 per year\n"
        "\n"
        "Adjustable Charges:\n"
        "Water Service Charges             64.69\n"
        "Wastewater Service Charges        0.00\n"
        "Fire Service Charges              0.00\n"
        "Notional / Usage Charges          5.94\n"
        "Miscellaneous / Adjustments / Credits  -70.63\n"
        "\n"
        "Non Adjustable Charges:\n"
        "Total Outstanding: 0.00\n"
    )

    def test_returns_only_positive_service_charges(self):
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            _parse_gippsland_stacked,
        )
        rows = _parse_gippsland_stacked(self.SAMPLE_TEXT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].label, "Water Service Charges")
        self.assertAlmostEqual(rows[0].amount, 64.69, places=2)
        self.assertEqual(rows[0].source, "gippsland_stacked")

    def test_period_days_derived_from_billing_periods(self):
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            _parse_gippsland_stacked,
        )
        rows = _parse_gippsland_stacked(self.SAMPLE_TEXT)
        # 365 / 3 = 121.67 → round = 122, which maps to multiplier 3
        self.assertEqual(rows[0].period_days, 122)

    def test_excludes_usage_and_credit_rows(self):
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            _parse_gippsland_stacked,
        )
        rows = _parse_gippsland_stacked(self.SAMPLE_TEXT)
        labels = [r.label for r in rows]
        self.assertNotIn("Notional / Usage Charges", labels)
        self.assertNotIn("Miscellaneous / Adjustments / Credits", labels)

    def test_skips_when_no_trigger(self):
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            _parse_gippsland_stacked,
        )
        rows = _parse_gippsland_stacked("Water Service Charges  64.69\n")
        self.assertEqual(rows, [])

    def test_annual_amount_via_pipeline(self):
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            _run_pipeline,
            _get_template,
            _detect_authority,
        )
        authority = _detect_authority(self.SAMPLE_TEXT)
        template = _get_template(authority)
        result = _run_pipeline(self.SAMPLE_TEXT, authority=authority, template=template)
        self.assertAlmostEqual(result["annual_amount"], 194.07, places=2)

    def test_breakdown_multiplier(self):
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            _run_pipeline,
            _get_template,
            _detect_authority,
        )
        authority = _detect_authority(self.SAMPLE_TEXT)
        template = _get_template(authority)
        result = _run_pipeline(self.SAMPLE_TEXT, authority=authority, template=template)
        self.assertEqual(result["breakdown"][0]["label"], "Water Service Charges")
        self.assertEqual(result["breakdown"][0]["multiplier"], 3)


# ---------------------------------------------------------------------------
# Snapshot writer
# ---------------------------------------------------------------------------


class TestSnapshotWriter(unittest.TestCase):
    """_save_snapshot() writes JSON when TRICONVEY_WATER_SNAPSHOTS=1."""

    def test_snapshot_written_when_enabled(self):
        import json
        import os
        import tempfile
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            RawRow,
            ClassifiedRow,
            RowType,
            PropertyBlock,
            _save_snapshot,
        )

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRICONVEY_WATER_SNAPSHOTS"] = "1"
            os.environ["TRICONVEY_WATER_SNAPSHOT_DIR"] = tmp
            # Reload module-level constants that were captured at import time
            import triconvey_agent.canonical.extractors.water_authority_certificate_v2 as mod
            mod._SNAPSHOTS_ENABLED = True
            from pathlib import Path
            mod._SNAPSHOT_DIR = Path(tmp)
            try:
                raw = RawRow("Water Service Charge", 52.50, 91, source="period_line")
                cr = ClassifiedRow(raw=raw, row_type=RowType.recurring_charge, multiplier=4, annualised=210.0, confidence=0.94)
                block = PropertyBlock(address="1/14 Bell St", lot_plan="1/LP12345", property_number="1234567", property_type="Residential", text="...", is_primary=True)

                _save_snapshot(
                    "Test Water Doc.pdf",
                    authority="Yarra Valley Water",
                    layout="period_row_table",
                    property_blocks=[block],
                    scoped_to_property_block=True,
                    scoped_property_number="1234567",
                    excluded_property_blocks_count=0,
                    raw_rows=[raw],
                    classified_rows=[cr],
                    excluded_rows=[],
                    winner_strategy="recurring_sum",
                    annual_amount=210.0,
                    confidence=0.94,
                    avg_parser_confidence=0.94,
                    warnings=[],
                )

                snap_path = Path(tmp) / "Test Water Doc.snapshot.json"
                self.assertTrue(snap_path.exists(), "snapshot file not created")
                data = json.loads(snap_path.read_text(encoding="utf-8"))
                self.assertEqual(data["snapshot_version"], 1)
                self.assertEqual(data["authority"], "Yarra Valley Water")
                self.assertEqual(data["layout"], "period_row_table")
                self.assertTrue(data["scoped_to_property_block"])
                self.assertEqual(data["scoped_property_number"], "1234567")
                self.assertEqual(data["excluded_property_blocks_count"], 0)
                self.assertEqual(data["annual_amount"], 210.0)
                self.assertEqual(data["winner_strategy"], "recurring_sum")
                self.assertEqual(len(data["raw_rows"]), 1)
                self.assertEqual(data["raw_rows"][0]["label"], "Water Service Charge")
                self.assertEqual(len(data["classified_rows"]), 1)
                self.assertEqual(data["classified_rows"][0]["row_type"], "recurring_charge")
                self.assertEqual(data["classified_rows"][0]["annualised"], 210.0)
                self.assertEqual(len(data["property_blocks"]), 1)
                self.assertTrue(data["property_blocks"][0]["is_primary"])
            finally:
                os.environ.pop("TRICONVEY_WATER_SNAPSHOTS", None)
                os.environ.pop("TRICONVEY_WATER_SNAPSHOT_DIR", None)

    def test_no_snapshot_when_disabled(self):
        import os
        import tempfile
        from triconvey_agent.canonical.extractors.water_authority_certificate_v2 import (
            _save_snapshot,
        )
        import triconvey_agent.canonical.extractors.water_authority_certificate_v2 as mod
        original = mod._SNAPSHOTS_ENABLED
        mod._SNAPSHOTS_ENABLED = False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                from pathlib import Path
                mod._SNAPSHOT_DIR = Path(tmp)
                _save_snapshot("Test.pdf", authority="X", layout="unknown", property_blocks=[], scoped_to_property_block=False, scoped_property_number=None, excluded_property_blocks_count=0, raw_rows=[], classified_rows=[], excluded_rows=[], winner_strategy="none", annual_amount=0.0, confidence=0.0, avg_parser_confidence=0.0, warnings=[])
                self.assertEqual(list(Path(tmp).iterdir()), [])
        finally:
            mod._SNAPSHOTS_ENABLED = original


if __name__ == "__main__":
    unittest.main()
