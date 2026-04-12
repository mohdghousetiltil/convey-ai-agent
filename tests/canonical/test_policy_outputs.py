from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.extractors.generic_doc_meta import (
    extract_generic_doc_meta_facts,
)
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.policy.attachments import build_attachments_text
from triconvey_agent.canonical.policy.computed import inject_computed_facts
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document, DocumentType, InputFileType


def make_fact(
    path: str,
    value,
    *,
    extractor: str = "rule:test",
    quote: str = "test quote",
    confidence: float = 0.95,
) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[Source(file="test.pdf", quote=quote, quote_verified=True)],
        extractor=extractor,
        extracted_at=datetime.utcnow(),
    )


def make_pdf_doc(filename: str, raw_text: str) -> Document:
    return Document(
        source_path=Path(filename),
        filename=filename,
        file_type=InputFileType.PDF,
        document_type=DocumentType.UNKNOWN,
        raw_text=raw_text,
    )


class TestGenericDocMetaDates(unittest.TestCase):
    def test_extracts_brimbank_building_approval_date(self):
        doc = make_pdf_doc(
            "2026-03 2273 - VIC Cert - Brimbank  Building Approval 326 (1).pdf",
            "BUILDING COMPLIANCE\nBRIMBANK CITY COUNCIL\nDATE: 18 March 2026",
        )
        facts = extract_generic_doc_meta_facts(doc)
        by_path = {fact.path: fact for fact in facts}
        self.assertEqual(
            by_path[P.DOCS_COUNCIL_BUILDING_APPROVAL_CERT_DATE].value,
            "18/03/2026",
        )

    def test_extracts_land_information_issue_date(self):
        doc = make_pdf_doc(
            "2026-03 2273 - VIC Cert - Brimbank  Land Information Certificate.pdf",
            "Assessment Number: 926204 Issue date: 23/03/2026",
        )
        facts = extract_generic_doc_meta_facts(doc)
        by_path = {fact.path: fact for fact in facts}
        self.assertEqual(
            by_path[P.DOCS_COUNCIL_LAND_INFO_CERT_DATE].value,
            "23/03/2026",
        )

    def test_extracts_ballarat_certificate_issue_dates_from_dash_format(self):
        doc = make_pdf_doc(
            "VIC_ Enquiry - Ballarat_ Building Approval 326 (1).pdf",
            "Final Certificate 31 May 2017\nIssue date: 31-Mar-2026",
        )
        facts = extract_generic_doc_meta_facts(doc)
        by_path = {fact.path: fact for fact in facts}
        self.assertEqual(
            by_path[P.DOCS_COUNCIL_BUILDING_APPROVAL_CERT_DATE].value,
            "31/03/2026",
        )

    def test_extracts_residential_tenancy_agreement_date(self):
        doc = make_pdf_doc(
            "lease - Jordy Place 6 - Scott - LA 03.06.2025.pdf",
            "Residential Rental Agreement\nDate of agreement\n27 / 03 / 2025",
        )
        facts = extract_generic_doc_meta_facts(doc)
        by_path = {fact.path: fact for fact in facts}
        self.assertEqual(
            by_path[P.DOCS_RESIDENTIAL_TENANCY_AGREEMENT_DATE].value,
            "27/03/2025",
        )

    def test_extracts_oc_basic_report_produced_date_not_legacy_body_date(self):
        doc = make_pdf_doc(
            "2026-03 2273 - VIC OC Basic - Owners Corporation Basic Report - 1 PS502358G.pdf",
            "Produced: 13/03/2026 05:53:11 PM\nFrom 31 December 2007 every Body Corporate is deemed...",
        )
        facts = extract_generic_doc_meta_facts(doc)
        by_path = {fact.path: fact for fact in facts}
        self.assertEqual(by_path[P.DOCS_OC_BASIC_REPORT_DATE].value, "13/03/2026")

    def test_extracts_report_date_with_abbreviated_month(self):
        doc = make_pdf_doc(
            "Lotsearch Pty Ltd_  - 34 GLINDEN AVENUE, ARDEER VIC 3022.pdf",
            "EPA Priority Sites Register Plus+\nREPORT DATE\n13 Mar 2026",
        )
        facts = extract_generic_doc_meta_facts(doc)
        by_path = {fact.path: fact for fact in facts}
        self.assertEqual(by_path[P.DOCS_EPA_CERT_DATE].value, "13/03/2026")

    def test_extracts_covenant_instrument_number_and_date(self):
        doc = make_pdf_doc(
            "2026-03 2273 - VIC Instrument - Instrument Search - 1489008 (COVENANT).pdf",
            "Document Type\nInstrument\nDocument Identification\nNumber of Pages\n1489008\n2\nDocument Assembled\n13/03/2026 17:53",
        )
        facts = extract_generic_doc_meta_facts(doc)
        by_path = {fact.path: fact for fact in facts}
        self.assertEqual(
            by_path[P.DOCS_COVENANT].value,
            '{"number": "1489008", "date": "13/03/2026"}',
        )


class TestPolicyComputedOutputs(unittest.TestCase):
    def test_total_does_not_exceed_amount_is_computed_from_outgoings_plus_buffer(self):
        store = FactStoreImpl()
        store.add(make_fact(P.RATES_COUNCIL_ANNUAL, "$1,448.09", extractor="rule:vendor_form_v2"))
        store.add(make_fact(P.RATES_WATER_ANNUAL, "$640.00", extractor="rule:vendor_form_v2"))
        store.add(make_fact(P.RATES_LAND_TAX_AMOUNT, "$0.00", extractor="rule:land_tax_certificate_v1"))
        store.add(make_fact(P.RATES_OC_ANNUAL_AMOUNT, "$500.00", extractor="rule:oc_certificate_v1"))

        inject_computed_facts(store)

        self.assertEqual(
            store.get_value(P.POLICY_TAB1_TOTAL_DOES_NOT_EXCEED_AMOUNT),
            "$3,588.09",
        )

    def test_attachment_list_prefers_bundle_evidence_and_replaces_right_to_sell_line(self):
        store = FactStoreImpl()
        store.add(make_fact(P.TITLE_VOLUME, "10688", extractor="rule:vic_title_v1"))
        store.add(make_fact(P.TITLE_FOLIO, "989", extractor="rule:vic_title_v1"))
        store.add(
            make_fact(
                P.TITLE_PRODUCED_AT,
                "13/03/2026 05:53 PM",
                extractor="rule:vic_title_v1",
                quote="Produced 13/03/2026 05:53 PM",
            )
        )
        store.add(make_fact(P.DOCS_PLAN_OF_SUBDIVISION, '{"number": "PS502358G", "date": "13/03/2026"}'))
        store.add(make_fact(P.TITLE_HAS_COVENANT, True, extractor="rule:vic_title_v1"))
        store.add(make_fact(P.TITLE_ENCUMBRANCE_COUNT, 2, extractor="rule:vic_title_v1"))
        store.add(make_fact(P.title_encumbrance(1, "type"), "COVENANT", extractor="rule:vic_title_v1"))
        store.add(make_fact(P.title_encumbrance(1, "number"), "1489008", extractor="rule:vic_title_v1"))
        store.add(make_fact(P.title_encumbrance(1, "date"), "23/09/1931", extractor="rule:vic_title_v1"))
        store.add(make_fact(P.DOCS_COVENANT, '{"number": "1489008", "date": "13/03/2026"}'))
        store.add(make_fact(P.DOCS_OC_BASIC_REPORT_DATE, "13/03/2026"))
        store.add(make_fact(P.RATES_COUNCIL_AUTHORITY, "Brimbank City Council", extractor="rule:vendor_form_v2"))
        store.add(make_fact(P.DOCS_COUNCIL_BUILDING_APPROVAL_CERT_DATE, "18/03/2026"))
        store.add(make_fact(P.DOCS_COUNCIL_LAND_INFO_CERT_DATE, "23/03/2026"))
        store.add(make_fact(P.RATES_WATER_AUTHORITY, "Greater West Water", extractor="rule:vendor_form_v2"))
        store.add(
            make_fact(
                P.RATES_WATER_AUTHORITY,
                "Greater Western Water",
                extractor="rule:water_authority_certificate_v1",
            )
        )
        store.add(make_fact(P.DOCS_WATER_CERT_DATE, "19/03/2026", extractor="rule:water_authority_certificate_v1"))
        store.add(make_fact(P.DOCS_LAND_TAX_CERT_DATE, "25/03/2026", extractor="rule:land_tax_certificate_v1"))
        store.add(make_fact(P.DOCS_VICROADS_CERT_DATE, "13/03/2026"))
        store.add(make_fact(P.DOCS_EPA_CERT_DATE, "13/03/2026"))

        text = build_attachments_text(store)

        self.assertIn("- Plan of Subdivision 502358G dated 13/03/2026", text)
        self.assertIn("- Covenant 1489008 dated 13/03/2026", text)
        self.assertIn("- Owners Corporation Basic Report dated 13/03/2026", text)
        self.assertIn("- Owners Corporation Certificate", text)
        self.assertIn("- Brimbank City Council Building Approval Certificate dated 18/03/2026", text)
        self.assertIn("- Brimbank City Council Land Information Certificate dated 23/03/2026", text)
        self.assertIn("- Greater Western Water Encumbrance Certificate dated 19/03/2026", text)
        self.assertIn("- State Revenue Office Land Tax Certificate dated 25/03/2026", text)
        self.assertIn("- Owner Builder Report ***", text)
        self.assertNotIn("Any right to sell property docs", text)

    def test_attachment_list_uses_current_title_plan_adds_s173_and_tenancy(self):
        store = FactStoreImpl()
        title_source = Source(file="title.pdf", quote="Produced 31/03/2026", quote_verified=True)
        store.add(
            Fact(
                path=P.TITLE_PRODUCED_AT,
                value="31/03/2026 10:42 AM",
                confidence=0.99,
                sources=[title_source],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.TITLE_VOLUME,
                value="11586",
                confidence=0.99,
                sources=[Source(file="title.pdf", quote="VOLUME 11586", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.TITLE_FOLIO,
                value="024",
                confidence=0.99,
                sources=[Source(file="title.pdf", quote="FOLIO 024", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.DOCS_PLAN_OF_SUBDIVISION,
                value='{"number": "PS723695", "date": "19/06/2019"}',
                confidence=0.95,
                sources=[Source(file="lease.pdf", quote="plan number PS723695", quote_verified=True)],
                extractor="rule:generic_doc_meta_v1",
            )
        )
        store.add(
            Fact(
                path=P.TITLE_PLAN_TYPE,
                value="Plan of Subdivision",
                confidence=0.98,
                sources=[Source(file="title.pdf", quote="Plan of Subdivision 723695D", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.TITLE_PLAN_NUMBER,
                value="723695D",
                confidence=0.98,
                sources=[Source(file="title.pdf", quote="Plan of Subdivision 723695D", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(make_fact(P.TITLE_ENCUMBRANCE_COUNT, 2, extractor="rule:vic_title_v1"))
        store.add(
            Fact(
                path=P.title_encumbrance(0, "type"),
                value="AGREEMENT",
                confidence=0.98,
                sources=[Source(file="title.pdf", quote="AGREEMENT Section 173 Planning and Environment Act 1987", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.title_encumbrance(0, "number"),
                value="AL543380Q",
                confidence=0.98,
                sources=[Source(file="title.pdf", quote="AL543380Q 08/12/2014", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.title_encumbrance(0, "text"),
                value="AGREEMENT Section 173 Planning and Environment Act 1987 AL543380Q 08/12/2014",
                confidence=0.98,
                sources=[Source(file="title.pdf", quote="AGREEMENT Section 173 Planning and Environment Act 1987 AL543380Q 08/12/2014", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.title_encumbrance(1, "type"),
                value="AGREEMENT",
                confidence=0.98,
                sources=[Source(file="title.pdf", quote="AGREEMENT Section 173 Planning and Environment Act 1987", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.title_encumbrance(1, "number"),
                value="AL969078N",
                confidence=0.98,
                sources=[Source(file="title.pdf", quote="AL969078N 19/06/2015", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(
            Fact(
                path=P.title_encumbrance(1, "text"),
                value="AGREEMENT Section 173 Planning and Environment Act 1987 AL969078N 19/06/2015",
                confidence=0.98,
                sources=[Source(file="title.pdf", quote="AGREEMENT Section 173 Planning and Environment Act 1987 AL969078N 19/06/2015", quote_verified=True)],
                extractor="rule:vic_title_v1",
            )
        )
        store.add(make_fact(P.RATES_COUNCIL_AUTHORITY, "Ballarat City Council", extractor="rule:vendor_form_v2"))
        store.add(make_fact(P.DOCS_COUNCIL_BUILDING_APPROVAL_CERT_DATE, "31/03/2026"))
        store.add(make_fact(P.DOCS_COUNCIL_LAND_INFO_CERT_DATE, "31/03/2026"))
        store.add(make_fact(P.RATES_WATER_AUTHORITY, "Central Highlands Water", extractor="rule:vendor_form_v2"))
        store.add(make_fact(P.DOCS_WATER_CERT_DATE, "31/03/2026"))
        store.add(make_fact(P.DOCS_LAND_TAX_CERT_DATE, "31/03/2026"))
        store.add(make_fact(P.DOCS_PROPERTY_REPORT_DATE, "31/03/2026"))
        store.add(make_fact(P.DOCS_VICROADS_CERT_DATE, "31/03/2026"))
        store.add(make_fact(P.DOCS_EPA_CERT_DATE, "31/03/2026"))
        store.add(make_fact(P.DOCS_RESIDENTIAL_TENANCY_AGREEMENT_DATE, "27/03/2025"))

        text = build_attachments_text(store)

        self.assertIn("- Register Search Statement Volume 11586 Folio 024 dated 31/03/2026", text)
        self.assertIn("- Plan of Subdivision 723695D dated 31/03/2026", text)
        self.assertNotIn("- Plan of Subdivision 723695 dated 19/06/2019", text)
        self.assertIn("- Section 173 Agreement AL543380Q dated 31/03/2026", text)
        self.assertIn("- Section 173 Agreement AL969078N dated 31/03/2026", text)
        self.assertIn("- City of Ballarat Building Approval Certificate dated 31/03/2026", text)
        self.assertIn("- City of Ballarat Land Information Certificate dated 31/03/2026", text)
        self.assertIn("- Central Highlands Water Encumbrance Certificate dated 31/03/2026", text)
        self.assertIn("- Residential Tenancy Agreement dated 27/03/2025", text)


if __name__ == "__main__":
    unittest.main()
