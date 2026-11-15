"""Page-level retrieval: picks the right pages from long documents and bulk uploads."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from triconvey_agent.canonical.extractors import omni_perception
from triconvey_agent.corpus import page_index


FILLER = "General terms and conditions apply to this certificate. " * 60  # ~3,400 chars


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    """Keyword-only scoring, so tests never call the network."""
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _long_council_pages() -> list[tuple[int, str]]:
    pages = [(n, f"Page {n}. {FILLER}") for n in range(1, 13)]
    pages[8] = (9, "Rates and charges levied this year. General rates $1,120.00. "
                   "Municipal charge $177.59. Total payable $1,297.59. Instalments paid $0.00.")
    return pages


def test_short_documents_are_sent_whole():
    pages = [(1, "Total payable $500.00"), (2, "Page two")]
    assert page_index.select_relevant_pages(pages, "total payable", char_budget=1_000) == pages


def test_long_document_keeps_the_rates_page_in_page_order():
    selected = page_index.select_relevant_pages(
        _long_council_pages(), page_index.DOC_TYPE_QUERIES["council"], char_budget=8_000
    )
    numbers = [n for n, _ in selected]
    assert 9 in numbers
    assert numbers == sorted(numbers)
    assert sum(len(t) for _, t in selected) <= 8_000


def test_one_oversized_page_is_cut_to_its_relevant_part():
    page = "x " * 10_000 + "Total payable $1,297.59 for rates levied this year " + "y " * 10_000
    selected = page_index.select_relevant_pages([(1, page), (2, FILLER)], "total payable rates", char_budget=2_000)
    assert len(selected) == 1
    assert "$1,297.59" in selected[0][1]
    assert len(selected[0][1]) <= 2_000


def test_embeddings_change_the_ranking_when_available():
    pages = [(1, "alpha " * 200), (2, "beta " * 200)]

    def fake_embed(texts):
        # Page 2 and the query point the same way; page 1 does not.
        return [[1.0, 0.0], [0.0, 1.0]] + [[0.0, 1.0]]

    selected = page_index.select_relevant_pages(pages, "unrelated words", char_budget=1_100, embed_fn=fake_embed)
    assert [n for n, _ in selected] == [2]


def test_omni_sends_relevant_pages_for_long_documents():
    pages = _long_council_pages()
    text = "\n\n".join(t for _, t in pages)
    sent = omni_perception._relevant_text("council", text, pages)
    assert "Total payable $1,297.59" in sent
    assert "[Page 9 of 12]" in sent
    assert len(sent) <= omni_perception._MAX_DOCUMENT_CHARS


def test_omni_leaves_short_documents_unchanged():
    assert omni_perception._relevant_text("water", "Quarterly charges $286.03", [(1, "x")]) == "Quarterly charges $286.03"


@pytest.fixture
def bulk_run(tmp_path, monkeypatch) -> Path:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    files = {
        "vendor bundle.pdf": [f"Vendor bundle page {n}. {FILLER}" for n in range(1, 40)],
        "water information statement.pdf": [
            "Greater Western Water information statement.",
            "Service charges for the quarter: water $86.03, sewerage $200.00. Total $286.03 per quarter.",
        ],
    }
    for name in files:
        (uploads / name).write_bytes(b"%PDF-1.4 placeholder")

    def fake_read(path: Path):
        return [(i, text) for i, text in enumerate(files[path.name], start=1)]

    monkeypatch.setattr(page_index, "_read_pdf_pages", fake_read)
    return tmp_path


def test_search_finds_the_page_across_a_bulk_upload(bulk_run):
    results = page_index.search_pages(bulk_run, "quarterly water sewerage service charges", top_k=3)
    assert results[0]["file"] == "water information statement.pdf"
    assert results[0]["page"] == 2
    assert "$286.03" in results[0]["snippet"]
    assert page_index.index_stats(bulk_run) == {"pages": 41, "documents": 2}


def test_read_page_returns_full_text_and_reports_missing_pages(bulk_run):
    page = page_index.read_page(bulk_run, "Water Information Statement.pdf", 2)
    assert "Total $286.03 per quarter" in page["text"]
    missing = page_index.read_page(bulk_run, "water information statement.pdf", 9)
    assert "error" in missing
    assert "vendor bundle.pdf" in missing["available_files"]


def test_index_is_reused_until_uploads_change(bulk_run, monkeypatch):
    page_index.search_pages(bulk_run, "water")
    calls = []
    original = page_index._read_pdf_pages
    monkeypatch.setattr(page_index, "_read_pdf_pages", lambda p: calls.append(p) or original(p))
    page_index.search_pages(bulk_run, "water")
    assert calls == []
    saved = json.loads((bulk_run / page_index.PAGE_INDEX_FILENAME).read_text(encoding="utf-8"))
    assert saved["embedding_model"] is None


def test_assistant_tools_search_then_read(bulk_run):
    from triconvey_agent.brain_f.tools import TOOL_DEFINITIONS, dispatch_tool
    from triconvey_agent.canonical.facts.store import FactStoreImpl

    names = {tool["name"] for tool in TOOL_DEFINITIONS}
    assert {"search_pages", "read_page"} <= names

    common = dict(store=FactStoreImpl(), memory={}, corpus_chunks=[], pending_patches=[], run_dir=bulk_run)
    found = json.loads(dispatch_tool("search_pages", {"query": "water service charges"}, **common))
    assert found["pages_searched"] == 41
    top = found["results"][0]
    page = json.loads(dispatch_tool("read_page", {"file": top["file"], "page": top["page"]}, **common))
    assert "$286.03" in page["text"]


def test_answer_cites_the_line_with_the_amount():
    from triconvey_agent.brain_f.agent import _page_citations

    pages = [{
        "file": "water information statement.pdf",
        "page": 2,
        "page_count": 2,
        "text": "Header line\nTotal $286.03 per quarter\nFooter",
    }]
    citations = _page_citations(pages, "The quarterly charge is $286.03, so $1,144.12 a year.", "water?")
    assert citations == [{
        "file": "water information statement.pdf",
        "page": 2,
        "page_count": 2,
        "quote": "Total $286.03 per quarter",
    }]


def test_citation_prefers_the_total_line_over_a_component():
    from triconvey_agent.brain_f.agent import _page_citations

    page = {
        "file": "bundle.pdf",
        "page": 23,
        "text": "Water service charge $41.20\nSewerage $118.75\nTotal service charges per quarter $182.00",
    }
    answer = "Water $41.20 and sewerage $118.75 make $182.00 a quarter, or $728.00 a year."
    citations = _page_citations([page], answer, "water charges")
    assert citations[0]["quote"] == "Total service charges per quarter $182.00"
