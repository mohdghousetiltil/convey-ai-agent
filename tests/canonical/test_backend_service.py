from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from triconvey_agent.backend.service import (
    _apply_preferred_autofill_filter,
    _apply_ai_review_overrides,
    _choose_bushfire_fact,
    _autofill_is_active,
    _coerce_ai_review_confidence,
    _coerce_review_value,
    _normalize_brain_f_mode,
    _suppress_owners_corporation_outgoing,
    _suppress_zero_outgoings,
    set_autofill_activity,
)
from triconvey_agent.canonical.schemas import AnswerObject, FormAction, FormActionPlan
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.schemas import Fact, Source


def make_fact(
    path: str,
    value,
    *,
    extractor: str,
    confidence: float = 0.9,
    quote: str = "test quote",
) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[Source(file="test.pdf", quote=quote, quote_verified=True)],
        extractor=extractor,
        extracted_at=datetime.now(UTC),
    )


class TestBackendBushfireFallback(unittest.TestCase):
    def test_prefers_authoritative_false_over_lower_authority_true(self):
        store = FactStoreImpl()
        store.add(
            make_fact(
                "planning.bushfire_prone",
                False,
                extractor="rule:planning_certificate",
                confidence=0.82,
                quote="Designated Bushfire Prone Areas: No",
            )
        )
        store.add(
            make_fact(
                "planning.bushfire_prone",
                True,
                extractor="rule:vendor_form_v2",
                confidence=0.97,
                quote="Is the land in a bushfire prone area? Yes",
            )
        )

        winner = _choose_bushfire_fact(store)

        self.assertIsNotNone(winner)
        self.assertIs(winner.value, False)
        self.assertEqual(winner.extractor, "rule:planning_certificate")


class TestZeroOutgoingSuppression(unittest.TestCase):
    def test_zero_amount_blanks_authority_and_amount(self):
        answers = {
            "sec32_1.1_outgoing_3_authority": AnswerObject(
                question_id="sec32_1.1_outgoing_3_authority",
                question_label="Outgoing 3 authority",
                value="State Revenue Office",
                confidence=0.95,
                needs_review=False,
            ),
            "sec32_1.1_outgoing_3_amount": AnswerObject(
                question_id="sec32_1.1_outgoing_3_amount",
                question_label="Outgoing 3 amount",
                value="0.00",
                confidence=0.95,
                needs_review=False,
            ),
        }

        _suppress_zero_outgoings(answers)

        self.assertIsNone(answers["sec32_1.1_outgoing_3_authority"].value)
        self.assertIsNone(answers["sec32_1.1_outgoing_3_amount"].value)
        self.assertFalse(answers["sec32_1.1_outgoing_3_authority"].needs_review)
        self.assertFalse(answers["sec32_1.1_outgoing_3_amount"].needs_review)

    def test_blank_oc_row_when_no_active_oc_or_amount(self):
        store = FactStoreImpl()
        store.add(
            make_fact(
                "rates.owners_corporation.exists",
                False,
                extractor="rule:vendor_form_v2",
                confidence=0.95,
                quote="Owners corporation: No",
            )
        )
        answers = {
            "sec32_1.1_outgoing_4_authority": AnswerObject(
                question_id="sec32_1.1_outgoing_4_authority",
                question_label="Outgoing 4 authority",
                value="Owners Corporation 1",
                confidence=0.9,
                needs_review=False,
            ),
            "sec32_1.1_outgoing_4_amount": AnswerObject(
                question_id="sec32_1.1_outgoing_4_amount",
                question_label="Outgoing 4 amount",
                value=None,
                confidence=0.0,
                needs_review=False,
            ),
        }

        _suppress_owners_corporation_outgoing(answers, store)

        self.assertIsNone(answers["sec32_1.1_outgoing_4_authority"].value)
        self.assertIsNone(answers["sec32_1.1_outgoing_4_amount"].value)
        self.assertFalse(answers["sec32_1.1_outgoing_4_authority"].needs_review)
        self.assertFalse(answers["sec32_1.1_outgoing_4_amount"].needs_review)


class TestBrainFWarmupCoordination(unittest.TestCase):
    def test_autofill_activity_is_scoped_per_run_dir(self):
        run_dir = Path("C:/tmp/test-run")
        self.assertFalse(_autofill_is_active(run_dir))

        set_autofill_activity(run_dir, True)
        self.assertTrue(_autofill_is_active(run_dir))

        set_autofill_activity(run_dir, False)
        self.assertFalse(_autofill_is_active(run_dir))

    def test_brain_f_mode_aliases(self):
        self.assertEqual(_normalize_brain_f_mode("Basic"), "quick")
        self.assertEqual(_normalize_brain_f_mode("Normal"), "standard")
        self.assertEqual(_normalize_brain_f_mode("Deep"), "deep")


class TestPreferredAutofillFilter(unittest.TestCase):
    def test_always_keeps_sec32_6_final_items(self):
        plan = FormActionPlan(
            actions=[
                FormAction(
                    question_id="sec32_8_electricity_not_connected",
                    field_id="Sec. 32 (4)::CheckBox::Electricity supply::t720l-1875",
                    action="set_checkbox",
                    payload=False,
                    expected_after=False,
                ),
                FormAction(
                    question_id="policy_6_attachments",
                    field_id="Sec. 32 (6)::Edit::13. Attachments::t424l-1870",
                    action="set_text",
                    payload="- Due Diligence Checklist",
                    expected_after="- Due Diligence Checklist",
                ),
            ],
            review_gate_required=False,
        )

        filtered = _apply_preferred_autofill_filter(plan, ["sec32_8_electricity_not_connected"])

        by_question = {action.question_id: action for action in filtered.actions}
        self.assertEqual(by_question["sec32_8_electricity_not_connected"].action, "set_checkbox")
        self.assertEqual(by_question["policy_6_attachments"].action, "set_text")


class TestAiReviewOverlay(unittest.TestCase):
    def test_accepts_qualitative_confidence_labels(self):
        self.assertEqual(_coerce_ai_review_confidence("high"), 0.9)
        self.assertEqual(_coerce_ai_review_confidence("medium"), 0.7)
        self.assertEqual(_coerce_ai_review_confidence("low"), 0.4)
        self.assertEqual(_coerce_ai_review_confidence("nonsense"), 0.0)

    def test_applies_verified_ai_review_change_as_overlay(self):
        question = type("Q", (), {"id": "rates.owners_corporation.annual_amount", "expected_type": "string"})()
        answer = AnswerObject(
            question_id="rates.owners_corporation.annual_amount",
            question_label="Owners corporation annual amount",
            value="$400.00",
            confidence=0.91,
            needs_review=False,
        )
        result = _apply_ai_review_overrides(
            {"rates.owners_corporation.annual_amount": answer},
            {"rates.owners_corporation.annual_amount": question},
            {
                "rates.owners_corporation.annual_amount": {
                    "status": "suggest_change",
                    "suggested_value": "$425.00",
                    "confidence": "high",
                    "quote_verified": True,
                    "reason": "Certificate states the annual fee is $425.00.",
                    "source_file": "Owners Corporation Certificate.pdf",
                }
            },
        )
        updated = result["rates.owners_corporation.annual_amount"]
        self.assertEqual(updated.value, "$425.00")
        self.assertEqual(updated.presentation_hints["answer_origin"], "ai_review")
        self.assertEqual(updated.presentation_hints["authoritative_value"], "$400.00")
        self.assertEqual(updated.presentation_hints["field_id"], "rates.owners_corporation.annual_amount")

    def test_skips_unverified_ai_review_change(self):
        question = type("Q", (), {"id": "sec32_3.4_planning_scheme", "expected_type": "string"})()
        answer = AnswerObject(
            question_id="sec32_3.4_planning_scheme",
            question_label="Planning scheme",
            value="Brimbank",
            confidence=0.95,
            needs_review=False,
        )
        result = _apply_ai_review_overrides(
            {"sec32_3.4_planning_scheme": answer},
            {"sec32_3.4_planning_scheme": question},
            {
                "sec32_3.4_planning_scheme": {
                    "status": "suggest_change",
                    "suggested_value": "Melton",
                    "confidence": 0.99,
                    "quote_verified": False,
                }
            },
        )
        self.assertEqual(result["sec32_3.4_planning_scheme"].value, "Brimbank")

    def test_coerces_boolean_review_values(self):
        question = type("Q", (), {"id": "sec32_oc_inactive", "expected_type": "bool"})()
        self.assertTrue(_coerce_review_value(question, "yes"))
        self.assertFalse(_coerce_review_value(question, "no"))



if __name__ == "__main__":
    unittest.main()
