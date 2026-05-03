"""Tests for council rates certificate / land information statement extractor."""
import unittest
from pathlib import Path

from triconvey_agent.canonical.extractors.council_rates_certificate import (
    extract_council_rates_certificate_facts,
)
from triconvey_agent.schemas.documents import Document, DocumentPage, DocumentType, InputFileType


def _doc(filename: str, text: str) -> Document:
    """Helper to create a Document object for testing."""
    return Document(
        source_path=Path(filename),
        filename=filename,
        file_type=InputFileType.PDF,
        document_type=DocumentType.UNKNOWN,
        raw_text=text,
        normalized_text=text,
        pages=[DocumentPage(page_number=1, text=text, normalized_text=text)],
    )


REGRESSION_FIXTURES = [
    {
        "name": "Cardinia",
        "filename": "cardinia-rates.pdf",
        "text": """
        Land Information Certificate
        cardinia.vic.gov.au
        RATES & CHARGES
        ARREARS BROUGHT FORWARD
        RATES
        INTEREST
        LEVIED
        GARBAGE
        BALANCE
        SPECIAL RATES /SPECIAL CHARGES
        $0.00
        $0.00
        $1,215.80 $912.00
        $384.50 $288.37
        MUNICIPAL CHARGE $0.00 $0.00
        GREEN WASTE LEVY $109.45 $82.09
        EMERGENCY SERVICES & VOLUNTEERS FUND $226.83 $170.12
        TOTAL OUTSTANDING $1,452.58
        """,
        "expected_authority": "Cardinia Shire Council",
        "expected_annual": "$1,936.58",
    },
    {
        "name": "Glen Eira",
        "filename": "glen-eira-rates.pdf",
        "text": """
        Glen Eira City Council
        Land Information Certificate
        General Rates   ESVF   Garbage Charge   Total
        Current Rates
        Levied 2025/2026   402.80   186.15   345.00   $933.95
        Payments          -293.14  -135.48  -251.08  ($679.70)
        Balance
        Outstanding        109.86    50.77    94.07   $254.70
        Summary of Charges Outstanding
        General Rates, Charges & ESVF $254.70
        """,
        "expected_authority": "Glen Eira City Council",
        "expected_annual": "$933.95",
    },
]


class TestCouncilRatesCertificateExtractor(unittest.TestCase):
    """Test council rates certificate extraction for various Victorian council formats."""

    def test_knox_city_council_format(self):
        """Test Knox City Council Land Information Certificate format.
        
        Knox format uses:
        - "Less Payments received $-1,879.90"
        - "Total balance payable $626.00"
        - "Sub total $2,505.90" (annual levy before payments)
        
        Annual amount should be: $1,879.90 + $626.00 = $2,505.90
        """
        text = """
        Knox City Council Land Information Certificate
        For the period 1 July 2025 to 30 June 2026
        Section 121 Local Government Act 2020
        
        Property location 14 Mistletoe Close
        KNOXFIELD VIC 3180
        
        Rates and charges                                    Levied        Balance
        Municipal Rates                                      1,710.80
        Optional Waste Charges                               48.50
        Residential Waste Charges                            415.15
        Optional Organics Waste Charges                      0.00
        Emergency Services Volunteers Fund                   331.45
        State Landfill Levy                                  $0.00
        Sub total                                            $2,505.90
        Less Pensioner concession/rebate                     $0.00
        Less Payments received                               $-1,879.90
        Total balance payable                                $626.00
        """
        
        doc = _doc(
            "VIC-Enquiry-Knox-Land-Information-Certificate.pdf",
            text,
        )
        
        facts = extract_council_rates_certificate_facts(doc)
        
        # Should extract council name
        council_facts = [f for f in facts if f.path == "rates.council.authority_name"]
        self.assertEqual(len(council_facts), 1)
        self.assertEqual(council_facts[0].value, "Knox City Council")
        self.assertGreaterEqual(council_facts[0].confidence, 0.90)
        
        # Should extract annual amount using paid + outstanding
        amount_facts = [f for f in facts if f.path == "rates.council.annual_amount"]
        self.assertEqual(len(amount_facts), 1)
        self.assertEqual(amount_facts[0].value, "$2,505.90")
        self.assertGreaterEqual(amount_facts[0].confidence, 0.95)
        self.assertIn("paid", amount_facts[0].notes.lower())
        self.assertIn("outstanding", amount_facts[0].notes.lower())

    def test_knox_sub_total_fallback(self):
        """Test Knox format when only Sub total line is available."""
        text = """
        Knox City Council Land Information Certificate
        
        Rates and charges                                    Levied        Balance
        Municipal Rates                                      1,710.80
        Residential Waste Charges                            415.15
        Emergency Services Volunteers Fund                   331.45
        Sub total                                            $2,505.90
        """
        
        doc = _doc("knox-rates.pdf", text)
        
        facts = extract_council_rates_certificate_facts(doc)
        
        # Should extract annual amount from Sub total line
        amount_facts = [f for f in facts if f.path == "rates.council.annual_amount"]
        self.assertEqual(len(amount_facts), 1)
        self.assertEqual(amount_facts[0].value, "$2,505.90")
        self.assertGreaterEqual(amount_facts[0].confidence, 0.95)
        self.assertIn("sub total", amount_facts[0].notes.lower())

    def test_brimbank_format(self):
        """Test Brimbank City Council format (original format)."""
        text = """
        Brimbank City Council
        Land Information Certificate
        
        Less Payments: -$1,208.97
        Total Rates & Charges Due: $507.78
        """
        
        doc = _doc("brimbank-lic.pdf", text)
        
        facts = extract_council_rates_certificate_facts(doc)
        
        # Should extract annual amount: 1208.97 + 507.78 = 1716.75
        amount_facts = [f for f in facts if f.path == "rates.council.annual_amount"]
        self.assertEqual(len(amount_facts), 1)
        self.assertEqual(amount_facts[0].value, "$1,716.75")
        self.assertGreaterEqual(amount_facts[0].confidence, 0.95)

    def test_ballarat_format(self):
        """Test Ballarat City Council format."""
        text = """
        City of Ballarat
        Land Information Certificate
        
        Less Payments Received-2,349.87
        TOTAL OUTSTANDING 0.00
        """
        
        doc = _doc("ballarat-lic.pdf", text)
        
        facts = extract_council_rates_certificate_facts(doc)
        
        # Should extract annual amount: 2349.87 + 0.00 = 2349.87
        amount_facts = [f for f in facts if f.path == "rates.council.annual_amount"]
        self.assertEqual(len(amount_facts), 1)
        self.assertEqual(amount_facts[0].value, "$2,349.87")
        self.assertGreaterEqual(amount_facts[0].confidence, 0.95)

    def test_no_match_returns_empty(self):
        """Test that non-council documents return no facts."""
        text = """
        This is a water bill from Yarra Valley Water.
        Account summary only.
        """
        
        doc = _doc("water-bill.pdf", text)
        
        facts = extract_council_rates_certificate_facts(doc)
        self.assertEqual(len(facts), 0)

    def test_monash_multiple_charges_and_balance_owing(self):
        text = """
        Land Information Certificate
        Rates & Charges - Multiple assessments may apply for the year ending 30 June 2026:
        Residential/Supplementary Rate 1,839.75
        Recycle and Waste Charge 65.00
        Emergency Services and Volunteers Fund - State Gov 355.70
        Residential Waste 246.30
        Payments -1,880.75
        BALANCE OWING Assessment No. 1058817 $626.00
        """

        doc = _doc(
            "VIC_ Enquiry - Monash_ Land Information Certificate - 8001_131.pdf",
            text,
        )
        facts = extract_council_rates_certificate_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path["rates.council.authority_name"].value, "Monash City Council")
        self.assertEqual(by_path["rates.council.annual_amount"].value, "$2,506.75")
        self.assertIn("negative adjustments", by_path["rates.council.annual_amount"].notes.lower())

    def test_maroondah_filename_authority_and_negative_plus_final_total(self):
        text = """
        LAND INFORMATION CERTIFICATE
        Less Payments
        -1,150.35
        ASSESSMENT TOTAL $1,148.00
        TOTAL BALANCE $1,148.00
        """

        doc = _doc(
            "VIC_ Enquiry - Maroondah_ Land Information Certificate - 8203_837.pdf",
            text,
        )
        facts = extract_council_rates_certificate_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path["rates.council.authority_name"].value, "Maroondah City Council")
        self.assertEqual(by_path["rates.council.annual_amount"].value, "$2,298.35")

    def test_maroondah_multiline_negative_amount(self):
        text = """
        LAND INFORMATION CERTIFICATE
        Less Payments
        Less Overpayments
        -1,150.35
        0.00
        ASSESSMENT TOTAL $1,148.00
        TOTAL BALANCE $1,148.00
        """

        doc = _doc(
            "VIC_ Enquiry - Maroondah_ Land Information Certificate - 8203_837.pdf",
            text,
        )
        facts = extract_council_rates_certificate_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path["rates.council.annual_amount"].value, "$2,298.35")

    def test_baw_baw_sub_total_beats_negative_plus_balance(self):
        text = """
        Land Information Certificate
        Current Years Rates and Charges Sub Total 3,341.70
        Pension Rebate -341.00
        Payments Received -1,500.70
        TOTAL BALANCE OUTSTANDING 1,500.00
        """

        doc = _doc(
            "VIC_ Enquiry - Baw Baw_ Land Information Certificate - 9045_910, 8988_215.pdf",
            text,
        )
        facts = extract_council_rates_certificate_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path["rates.council.authority_name"].value, "Baw Baw Shire Council")
        self.assertEqual(by_path["rates.council.annual_amount"].value, "$3,341.70")
        self.assertIn("sub total", by_path["rates.council.annual_amount"].notes.lower())

    def test_maroondah_table_rebates_are_included_in_annual_total(self):
        text = """
        LAND INFORMATION CERTIFICATE
        FINANCIAL INFORMATION
        RATES & CHARGES LEVIED REBATES BALANCE
        Arrears 0.00
        General Rate 2,046.10 -266.00 1,780.10
        Waste Service Charge 465.00 0.00 465.00
        State Government Fire Levy MFB 0.00 0.00 0.00
        State Government ESVF Levy 317.65 -50.00 267.65
        Municipal Charge 0.00 0.00 0.00
        Bank Fees 0.00 0.00 0.00
        Refund 0.00
        Less Payments
        -1,256.75
        ASSESSMENT TOTAL $1,256.00
        TOTAL BALANCE $1,256.00
        """

        doc = _doc(
            "VIC_ Enquiry - Maroondah_ Land Information Certificate - 9658_531.pdf",
            text,
        )
        facts = extract_council_rates_certificate_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path["rates.council.annual_amount"].value, "$2,828.75")
        self.assertIn("rebates $316.00", by_path["rates.council.annual_amount"].notes)

    def test_merri_bek_certificate_not_falsely_excluded_as_water(self):
        text = """
        LAND INFORMATION CERTIFICATE
        SECTION 121 LOCAL GOVERNMENT ACT 2020
        Merri-bek City Council
        Rates and charges levied for the period 01/07/25 - 30/06/26
        Residential Rates $812.02
        Emergency Services Volunteer Fund - Residential $194.82
        Waste Management $294.59
        Rebates $0.00
        Payments/Adjustments $-651.43
        Net Total Outstanding $650.00
        Information in relation to any designated flood level may be obtained from Yarra Valley Water.
        """

        doc = _doc(
            "VIC_ Enquiry - Merri-Bek (Moreland)_ (Moreland) - Land Information Certificate.pdf",
            text,
        )
        facts = extract_council_rates_certificate_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path["rates.council.authority_name"].value, "Merri-bek City Council")
        self.assertEqual(by_path["rates.council.annual_amount"].value, "$1,301.43")

    def test_cardinia_scrambled_levied_balance_block_beats_inline_partial_sum(self):
        text = """
        Land Information Certificate
        cardinia.vic.gov.au
        RATES & CHARGES
        ARREARS BROUGHT FORWARD
        RATES
        INTEREST
        LEVIED
        GARBAGE
        BALANCE
        SPECIAL RATES /SPECIAL CHARGES
        $0.00
        $0.00
        $1,215.80 $912.00
        $384.50 $288.37
        MUNICIPAL CHARGE $0.00 $0.00
        GREEN WASTE LEVY $109.45 $82.09
        EMERGENCY SERVICES & VOLUNTEERS FUND $226.83 $170.12
        TOTAL OUTSTANDING $1,452.58
        """

        doc = _doc("cardinia-rates.pdf", text)
        facts = extract_council_rates_certificate_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path["rates.council.authority_name"].value, "Cardinia Shire Council")
        self.assertEqual(by_path["rates.council.annual_amount"].value, "$1,936.58")
        self.assertIn("scrambled_levied_balance_block", by_path["rates.council.annual_amount"].notes)

    def test_glen_eira_matrix_levied_row_beats_balance_outstanding(self):
        text = """
        Glen Eira City Council
        Land Information Certificate
        General Rates   ESVF   Garbage Charge   Total
        Current Rates
        Levied 2025/2026   402.80   186.15   345.00   $933.95
        Payments          -293.14  -135.48  -251.08  ($679.70)
        Balance
        Outstanding        109.86    50.77    94.07   $254.70
        Summary of Charges Outstanding
        General Rates, Charges & ESVF $254.70
        """

        doc = _doc("glen-eira-rates.pdf", text)
        facts = extract_council_rates_certificate_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path["rates.council.authority_name"].value, "Glen Eira City Council")
        self.assertEqual(by_path["rates.council.annual_amount"].value, "$933.95")
        self.assertIn("current_levied_matrix_row", by_path["rates.council.annual_amount"].notes)

    def test_regression_fixtures(self):
        for fixture in REGRESSION_FIXTURES:
            with self.subTest(fixture=fixture["name"]):
                doc = _doc(fixture["filename"], fixture["text"])
                facts = extract_council_rates_certificate_facts(doc)
                by_path = {fact.path: fact for fact in facts}

                if fixture.get("expected_authority"):
                    self.assertEqual(
                        by_path["rates.council.authority_name"].value,
                        fixture["expected_authority"],
                    )
                self.assertEqual(
                    by_path["rates.council.annual_amount"].value,
                    fixture["expected_annual"],
                )


if __name__ == "__main__":
    unittest.main()
