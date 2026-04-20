from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from triconvey_agent.backend.service import _chunk_page_text
from triconvey_agent.brain_f.agent import (
    _infer_field_answers,
    _load_chat_state,
    _save_chat_state,
)
from triconvey_agent.brain_f.tools import handle_compare_facts, handle_run_review_checklist
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.questions.loader import load_question_registry
from triconvey_agent.canonical.schemas import Fact, Source


def make_fact(
    path: str,
    value,
    *,
    extractor: str,
    confidence: float = 0.95,
    file: str = "test.pdf",
    quote: str = "test quote",
) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[Source(file=file, quote=quote, quote_verified=True)],
        extractor=extractor,
        extracted_at=datetime.now(UTC),
    )


class TestBrainFChunking(unittest.TestCase):
    def test_chunk_page_text_splits_long_page(self):
        text = " ".join([f"Sentence {i} contains several extra words for chunking coverage and retrieval precision." for i in range(1, 80)])
        chunks = _chunk_page_text("doc.pdf", 1, text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk["text"] for chunk in chunks))
        self.assertTrue(all("chunk_id" in chunk for chunk in chunks))


class TestBrainFTools(unittest.TestCase):
    def test_compare_facts_uses_specific_documents(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.water.annual_amount", "$640.00", extractor="vendor", file="Vendor Form.pdf"))
        store.add(make_fact("rates.water.annual_amount", "$737.01", extractor="cert", file="GWW Cert.pdf"))
        result = handle_compare_facts(
            store,
            path="rates.water.annual_amount",
            doc_a="vendor",
            doc_b="gww",
        )
        self.assertEqual(result["doc_a"]["value"], "$640.00")
        self.assertEqual(result["doc_b"]["value"], "$737.01")
        self.assertFalse(result["match"])

    def test_review_checklist_flags_conflict(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.water.annual_amount", "$640.00", extractor="vendor", confidence=0.80))
        store.add(make_fact("rates.water.annual_amount", "$737.01", extractor="cert", confidence=0.80))
        result = handle_run_review_checklist(store)
        water = next(item for item in result["items"] if item["label"] == "Water")
        self.assertEqual(water["status"], "warning")


class TestBrainFFieldAnswers(unittest.TestCase):
    def test_infers_owners_corporation_field_answers(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.owners_corporation.authority_name", "MCBM", extractor="oc_cert"))
        store.add(make_fact("rates.owners_corporation.annual_amount", "$1,971.80", extractor="oc_cert"))
        answers = _infer_field_answers(
            "rename owner corporation to MCBM",
            store,
            load_question_registry(),
        )
        question_ids = {item["question_id"] for item in answers}
        self.assertIn("sec32_1.1_outgoing_4_authority", question_ids)


class TestBrainFChatState(unittest.TestCase):
    def test_chat_state_round_trip(self):
        run_dir = Path(f"C:\\Users\\moham\\projects\\triconvey-agent\\.tmp_chat_state_{uuid4().hex}")
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            state = {"summary": "", "turns": []}
            _save_chat_state(run_dir, state, "What is the water charge?", "The annual water charge is $737.01.")
            loaded = _load_chat_state(run_dir)
            self.assertIn("water charge", loaded["summary"].lower())
            self.assertEqual(len(loaded["turns"]), 2)
            payload = json.loads((run_dir / "chat_history.json").read_text(encoding="utf-8"))
            self.assertIn("summary", payload)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
