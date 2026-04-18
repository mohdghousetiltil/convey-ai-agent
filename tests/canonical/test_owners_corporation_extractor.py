from __future__ import annotations

import unittest
from pathlib import Path

from triconvey_agent.canonical.extractors.owners_corporation import extract_owners_corporation_facts
from triconvey_agent.schemas.documents import Document, InputFileType


class TestOwnersCorporationExtractor(unittest.TestCase):
    def test_extracts_quarterly_fee_and_annualises(self):
        text = (
            "OWNERS CORPORATION CERTIFICATE\n"
            "The current fees for the above lot are $492.95 per quarter payable quarterly in advance and due on the First day of January, April, July and October each year."
        )
        doc = Document(
            source_path=Path("oc.pdf"),
            filename="VIC_ Enquiry - MBCM (Ballarat)_ 1_PS723695D - Section 151 Certificate from Owners Corporation.pdf",
            file_type=InputFileType.PDF,
            raw_text=text,
        )

        facts = extract_owners_corporation_facts(doc)
        by_path = {fact.path: fact for fact in facts}

        self.assertIn("rates.owners_corporation.exists", by_path)
        self.assertIn("rates.owners_corporation.annual_amount", by_path)
        self.assertEqual(by_path["rates.owners_corporation.annual_amount"].value, "$1,971.80")


if __name__ == "__main__":
    unittest.main()
