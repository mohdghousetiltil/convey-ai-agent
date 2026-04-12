"""Unit + integration tests for Brain B (Question Router).

The unit tests use synthetic Facts; the integration test at the bottom
runs the real vendor_form + vic_title extractors against the sample
PDFs and asserts a handful of registry questions answer correctly
end-to-end.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from triconvey_agent.canonical.extractors.vendor_form import (
    extract_vendor_form_facts,
)
from triconvey_agent.canonical.extractors.vic_title import (
    extract_vic_title_facts,
)
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.questions.loader import load_question_registry
from triconvey_agent.canonical.router import (
    answer_all_questions,
    answer_question,
)
from triconvey_agent.canonical.schemas import (
    AnswerStrategy,
    ClientOverride,
    ClientPolicy,
    ClientPreferences,
    Fact,
    Question,
    Source,
)
from triconvey_agent.ingest.pdf_loader import load_pdf_document

VENDOR_PDF = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "Vendor Instruction Form-Kyle Sultana.pdf"
)
TITLE_PDF = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "VIC_ Certificate - Register Search Statement (Copy of Title) Volume 12287 Folio 279.pdf"
)


def make_fact(
    path: str,
    value,
    *,
    extractor: str = "rule:test",
    confidence: float = 0.95,
    quote: str = "test quote",
) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[Source(file="test.pdf", quote=quote, quote_verified=True)],
        extractor=extractor,
        extracted_at=datetime.utcnow(),
    )


class StubAIClient:
    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text

    def complete(self, prompt: str):
        class _Result:
            def __init__(self, raw_text: str) -> None:
                self.raw_text = raw_text

        return _Result(self.raw_text)


# ===========================================================================
# Unit tests — synthetic facts
# ===========================================================================


class TestDeterministicDirect(unittest.TestCase):
    def test_direct_single_fact(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.council.annual_amount", "$1,234"))
        q = Question(
            id="q1",
            tab="T",
            label="Council amount",
            fact_paths=["rates.council.annual_amount"],
            value_transform="direct",
        )
        ans = answer_question(q, store)
        self.assertEqual(ans.value, "$1,234")
        self.assertFalse(ans.needs_review)
        self.assertEqual(ans.facts_used, ["rates.council.annual_amount"])
        self.assertEqual(len(ans.evidence), 1)

    def test_missing_fact_escalates(self):
        store = FactStoreImpl()
        q = Question(
            id="q1",
            tab="T",
            label="Missing",
            fact_paths=["does.not.exist"],
        )
        ans = answer_question(q, store)
        self.assertIsNone(ans.value)
        self.assertTrue(ans.needs_review)
        self.assertTrue(any("no facts found" in r for r in ans.review_reasons))


class TestDeterministicNegate(unittest.TestCase):
    def test_negate_true_to_false(self):
        store = FactStoreImpl()
        store.add(make_fact("services.electricity.connected", True))
        q = Question(
            id="q",
            tab="T",
            label="Electricity NOT connected",
            fact_paths=["services.electricity.connected"],
            value_transform="negate",
        )
        ans = answer_question(q, store)
        self.assertIs(ans.value, False)
        self.assertFalse(ans.needs_review)

    def test_negate_false_to_true(self):
        store = FactStoreImpl()
        store.add(make_fact("services.gas.connected", False))
        q = Question(
            id="q",
            tab="T",
            label="Gas NOT connected",
            fact_paths=["services.gas.connected"],
            value_transform="negate",
        )
        self.assertIs(answer_question(q, store).value, True)

    def test_negate_rejects_non_bool(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.council.annual_amount", "$10"))
        q = Question(
            id="q",
            tab="T",
            label="x",
            fact_paths=["rates.council.annual_amount"],
            value_transform="negate",
        )
        ans = answer_question(q, store)
        self.assertIsNone(ans.value)
        self.assertTrue(ans.needs_review)


class TestDeterministicFirstPresent(unittest.TestCase):
    def test_returns_first_non_empty(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.council.authority_name", "City of Monash"))
        q = Question(
            id="q",
            tab="T",
            label="Outgoing authority",
            fact_paths=[
                "rates.outgoings.0.authority_name",
                "rates.council.authority_name",
            ],
            value_transform="first_present",
        )
        ans = answer_question(q, store)
        self.assertEqual(ans.value, "City of Monash")
        self.assertEqual(ans.facts_used, ["rates.council.authority_name"])

    def test_prefers_earlier_path_when_both_present(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.outgoings.0.authority_name", "Authoritative"))
        store.add(make_fact("rates.council.authority_name", "Fallback"))
        q = Question(
            id="q",
            tab="T",
            label="x",
            fact_paths=[
                "rates.outgoings.0.authority_name",
                "rates.council.authority_name",
            ],
            value_transform="first_present",
        )
        self.assertEqual(answer_question(q, store).value, "Authoritative")


class TestDeterministicAllFalse(unittest.TestCase):
    def test_telephone_or_nbn_one_connected_means_false(self):
        store = FactStoreImpl()
        store.add(make_fact("services.telephone.connected", False))
        store.add(make_fact("services.nbn.connected", True))  # NBN/VOIP counts
        q = Question(
            id="q",
            tab="T",
            label="Telephone NOT connected",
            fact_paths=["services.telephone.connected", "services.nbn.connected"],
            value_transform="all_false",
        )
        ans = answer_question(q, store)
        self.assertIs(ans.value, False, "tick should NOT fire when NBN provides phone")

    def test_neither_connected_means_true(self):
        store = FactStoreImpl()
        store.add(make_fact("services.telephone.connected", False))
        store.add(make_fact("services.nbn.connected", False))
        q = Question(
            id="q",
            tab="T",
            label="x",
            fact_paths=["services.telephone.connected", "services.nbn.connected"],
            value_transform="all_false",
        )
        self.assertIs(answer_question(q, store).value, True)


class TestOptionsValidation(unittest.TestCase):
    def test_value_outside_options_triggers_review(self):
        store = FactStoreImpl()
        store.add(make_fact("planning.zone", "MADE-UP-ZONE"))
        q = Question(
            id="q",
            tab="T",
            label="Zone",
            fact_paths=["planning.zone"],
            options=["GRZ - General Residential Zone", "NRZ - Neighbourhood Residential Zone"],
        )
        ans = answer_question(q, store)
        self.assertTrue(ans.needs_review)
        self.assertTrue(any("not one of the allowed options" in r for r in ans.review_reasons))


class TestConfidenceThreshold(unittest.TestCase):
    def test_below_threshold_triggers_review(self):
        store = FactStoreImpl()
        store.add(make_fact("a.b", "value", confidence=0.5))
        q = Question(id="q", tab="T", label="L", fact_paths=["a.b"])
        ans = answer_question(q, store, review_threshold=0.75)
        self.assertEqual(ans.value, "value")  # value still set, just flagged
        self.assertTrue(ans.needs_review)
        self.assertTrue(any("below review threshold" in r for r in ans.review_reasons))


class TestStrategyRouting(unittest.TestCase):
    def test_human_review_always_escalates(self):
        store = FactStoreImpl()
        store.add(make_fact("notices.orders", "some text"))
        q = Question(
            id="q",
            tab="T",
            label="Notices",
            fact_paths=["notices.orders"],
            answer_strategy=AnswerStrategy.HUMAN_REVIEW,
        )
        ans = answer_question(q, store)
        self.assertTrue(ans.needs_review)
        self.assertEqual(ans.answer_strategy, AnswerStrategy.HUMAN_REVIEW)
        self.assertIsNone(ans.value)
        # Evidence should still be carried for the reviewer
        self.assertEqual(len(ans.evidence), 1)

    def test_grounded_ai_without_client_escalates(self):
        store = FactStoreImpl()
        store.add(make_fact("insurance.home.company", "RACV"))
        q = Question(
            id="q",
            tab="T",
            label="Insurer",
            fact_paths=["insurance.home.company"],
            answer_strategy=AnswerStrategy.GROUNDED_AI,
        )
        ans = answer_question(q, store)
        self.assertTrue(ans.needs_review)
        self.assertEqual(ans.answer_strategy, AnswerStrategy.GROUNDED_AI)

    def test_grounded_ai_uses_ai_client_when_quote_is_verified(self):
        store = FactStoreImpl()
        store.add(make_fact("insurance.home.company", "RACV", quote="RACV Insurance"))
        q = Question(
            id="q",
            tab="T",
            label="Insurer",
            fact_paths=["insurance.home.company"],
            answer_strategy=AnswerStrategy.GROUNDED_AI,
            expected_type="string",
        )
        ai_client = StubAIClient(
            '{"value":"RACV","confidence":0.92,"quote":"RACV Insurance","source_file":"test.pdf","reason":"Explicit insurer name.","needs_review":false}'
        )
        ans = answer_question(q, store, ai_client=ai_client)
        self.assertEqual(ans.value, "RACV")
        self.assertFalse(ans.needs_review)
        self.assertEqual(ans.answer_strategy, AnswerStrategy.GROUNDED_AI)

    def test_grounded_ai_rejects_unverified_quote(self):
        store = FactStoreImpl()
        store.add(make_fact("insurance.home.company", "RACV", quote="RACV Insurance"))
        q = Question(
            id="q",
            tab="T",
            label="Insurer",
            fact_paths=["insurance.home.company"],
            answer_strategy=AnswerStrategy.GROUNDED_AI,
            expected_type="string",
        )
        ai_client = StubAIClient(
            '{"value":"RACV","confidence":0.92,"quote":"Not in evidence","source_file":"test.pdf","reason":"Explicit insurer name.","needs_review":false}'
        )
        ans = answer_question(q, store, ai_client=ai_client)
        self.assertTrue(ans.needs_review)
        self.assertTrue(any("quote" in reason for reason in ans.review_reasons))

    def test_policy_default_without_policy_escalates(self):
        store = FactStoreImpl()
        q = Question(
            id="q",
            tab="T",
            label="Boilerplate",
            answer_strategy=AnswerStrategy.POLICY_DEFAULT,
        )
        ans = answer_question(q, store)
        self.assertTrue(ans.needs_review)
        self.assertEqual(ans.answer_strategy, AnswerStrategy.POLICY_DEFAULT)


class TestClientOverride(unittest.TestCase):
    def test_override_wins_over_facts(self):
        store = FactStoreImpl()
        store.add(make_fact("planning.zone", "GRZ"))
        q = Question(
            id="q1",
            tab="T",
            label="Zone",
            fact_paths=["planning.zone"],
        )
        policy = ClientPolicy(
            preferences=ClientPreferences(),
            overrides=[
                ClientOverride(
                    question_id="q1",
                    value="ALWAYS-NRZ",
                    reason="client says all properties in this dev are NRZ",
                )
            ],
        )
        ans = answer_question(q, store, policy=policy)
        self.assertEqual(ans.value, "ALWAYS-NRZ")
        self.assertEqual(ans.confidence, 1.0)
        self.assertFalse(ans.needs_review)
        self.assertIn("client_override_reason", ans.presentation_hints)


# ===========================================================================
# Integration test — real PDFs + real registry
# ===========================================================================


@unittest.skipUnless(
    VENDOR_PDF.exists() and TITLE_PDF.exists(),
    "sample PDFs missing",
)
class TestEndToEndAnsweringRealPDFs(unittest.TestCase):
    """vendor_form + vic_title → FactStore → router → AnswerObjects."""

    @classmethod
    def setUpClass(cls):
        vendor_doc = load_pdf_document(VENDOR_PDF)
        title_doc = load_pdf_document(TITLE_PDF)

        store = FactStoreImpl()
        store.add_many(extract_vendor_form_facts(vendor_doc))
        store.add_many(extract_vic_title_facts(title_doc))
        cls.store = store

        cls.registry = load_question_registry()
        cls.answers = answer_all_questions(cls.registry.values(), store)

    def test_every_question_in_registry_has_an_answer(self):
        self.assertEqual(set(self.answers), set(self.registry))

    def test_electricity_not_connected_is_false(self):
        ans = self.answers["sec32_8_electricity_not_connected"]
        self.assertIs(ans.value, False, "electricity is connected → NOT-connected = False")
        self.assertFalse(ans.needs_review)

    def test_gas_not_connected_is_false(self):
        self.assertIs(self.answers["sec32_8_gas_not_connected"].value, False)

    def test_water_not_connected_is_false(self):
        self.assertIs(self.answers["sec32_8_water_not_connected"].value, False)

    def test_sewerage_not_connected_is_false(self):
        self.assertIs(self.answers["sec32_8_sewerage_not_connected"].value, False)

    def test_telephone_not_connected_is_true_pstn_not_connected(self):
        """Traditional telephone (PSTN) is NOT connected — tick the box (True).
        NBN/VOIP is a separate service and does NOT affect this checkbox.
        The registry uses negate on services.telephone.connected only."""
        ans = self.answers["sec32_8_telephone_not_connected"]
        self.assertIs(ans.value, True)
        self.assertEqual(ans.facts_used, ["services.telephone.connected"])

    def test_outgoing_1_authority_is_council(self):
        ans = self.answers["sec32_1.1_outgoing_1_authority"]
        self.assertEqual(ans.value, "City of Monash")
        self.assertFalse(ans.needs_review)

    def test_outgoing_1_amount_from_council(self):
        ans = self.answers["sec32_1.1_outgoing_1_amount"]
        self.assertEqual(ans.value, "$1,198.25")

    def test_planning_zone_escalates_when_no_planning_certificate(self):
        """We have no planning_certificate extractor yet → no fact at planning.zone."""
        ans = self.answers["sec32_3.4_planning_zone"]
        self.assertIsNone(ans.value)
        self.assertTrue(ans.needs_review)

    def test_human_review_question_escalates(self):
        ans = self.answers["sec32_4.1_notices_text"]
        self.assertTrue(ans.needs_review)
        self.assertEqual(ans.answer_strategy, AnswerStrategy.HUMAN_REVIEW)

    def test_policy_default_question_escalates_without_policy(self):
        ans = self.answers["sec32_1.2_no_other_amounts"]
        self.assertTrue(ans.needs_review)
        self.assertEqual(ans.answer_strategy, AnswerStrategy.POLICY_DEFAULT)

    def test_grounded_ai_question_escalates_without_ai_client(self):
        ans = self.answers["sec32_2.1_insurance_company"]
        self.assertTrue(ans.needs_review)
        self.assertEqual(ans.answer_strategy, AnswerStrategy.GROUNDED_AI)


if __name__ == "__main__":
    unittest.main()
