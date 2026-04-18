from __future__ import annotations

import unittest
from datetime import UTC, datetime

from triconvey_agent.backend.service import _choose_bushfire_fact, _suppress_zero_outgoings
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


if __name__ == "__main__":
    unittest.main()
