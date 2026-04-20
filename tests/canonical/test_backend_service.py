from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from triconvey_agent.backend.service import (
    _choose_bushfire_fact,
    _autofill_is_active,
    _normalize_brain_f_mode,
    _suppress_owners_corporation_outgoing,
    _suppress_zero_outgoings,
    set_autofill_activity,
)
from triconvey_agent.canonical.schemas import AnswerObject
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


if __name__ == "__main__":
    unittest.main()
