from __future__ import annotations

import unittest
from pathlib import Path

from triconvey_agent.canonical.extractors.building_approval import extract_building_approval_facts
from triconvey_agent.canonical.extractors.paths import (
    POLICY_ATTACHMENTS_TEXT,
    POLICY_TAB3_BUILDING_PERMITS_AS_FOLLOWS,
    POLICY_TAB3_BUILDING_PERMITS_TEXT,
    RATES_COUNCIL_AUTHORITY,
    building_permit,
)
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.policy import run_policy_pass
from triconvey_agent.schemas.documents import Document, DocumentPage, DocumentType, InputFileType


def _doc(filename: str, text: str) -> Document:
    return Document(
        source_path=Path(filename),
        filename=filename,
        file_type=InputFileType.PDF,
        document_type=DocumentType.UNKNOWN,
        raw_text=text,
        normalized_text=text,
        pages=[DocumentPage(page_number=1, text=text, normalized_text=text)],
    )


class TestBuildingApprovalExtractor(unittest.TestCase):
    def test_ballarat_building_approval_extracts_permit_and_occupancy(self):
        text = """
Building Permit 10 Year Search
Application No. (if applicable) Description (if applicable)
BPA/2019/2050/P Construction of Dwelling, Attached Garage, Portico & Alfresco
Private Permit 30 October 2019
Occupancy Permit 18 May 2020
Issue date: 23-Mar-2026
"""
        facts = extract_building_approval_facts(_doc("VIC_ Enquiry - Ballarat_ Building Approval.pdf", text))
        by_path = {fact.path: fact for fact in facts}

        self.assertEqual(by_path[RATES_COUNCIL_AUTHORITY].value, "City of Ballarat")
        self.assertEqual(by_path[building_permit(0, "kind")].value, "building_permit")
        self.assertEqual(by_path[building_permit(0, "issue_date")].value, "30/10/2019")
        self.assertEqual(by_path[building_permit(1, "kind")].value, "occupancy_permit")
        self.assertEqual(by_path[building_permit(1, "issue_date")].value, "18/05/2020")

    def test_policy_builds_building_permit_text_and_attachments(self):
        text = """
Property Information Certificate
Indigo Shire Council
Permits, certificates of final inspection, notices and orders in the preceding 10 years
7632364376422 Glenn Colwell Construct Shed 11/10/2019 No No
"""
        store = FactStoreImpl()
        store.add_many(extract_building_approval_facts(_doc("VIC_ Enquiry - Indigo_ Building Approval.pdf", text)))

        run_policy_pass(store)

        permit_checked, _ = store.get(POLICY_TAB3_BUILDING_PERMITS_AS_FOLLOWS)
        permit_text, _ = store.get(POLICY_TAB3_BUILDING_PERMITS_TEXT)
        attachments, _ = store.get(POLICY_ATTACHMENTS_TEXT)

        self.assertIsNotNone(permit_checked)
        self.assertTrue(permit_checked.value)
        self.assertIsNotNone(permit_text)
        self.assertIn("Building Permit No. 7632364376422 issued on 11/10/2019", str(permit_text.value))
        self.assertIsNotNone(attachments)
        self.assertIn("- Building Permit No. 7632364376422 dated 11/10/2019", str(attachments.value))
