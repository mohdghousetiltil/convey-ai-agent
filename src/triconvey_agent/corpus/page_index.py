"""Page-level retrieval for large and bulk document sets.

Instead of sending the first few thousand characters of every document to the
model, this finds the pages that answer a question and sends only those.

Two entry points:

* ``select_relevant_pages`` works on pages already in memory. Extractors use it
  to pick the pages with the rates table out of a long certificate.
* ``build_page_index`` / ``search_pages`` / ``read_page`` keep a per-run index
  of every page of every uploaded PDF, so the assistant can search hundreds of
  pages and then read only the few that matter.

Scoring is hybrid: BM25 keyword relevance plus embedding similarity when an
embedding model is reachable. Without embeddings it is keyword-only, so it
still works offline and in tests.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from triconvey_agent.ai.embeddings import EMBEDDING_ID, embed_text, embed_texts, embeddings_available

LOG = logging.getLogger(__name__)

PAGE_INDEX_FILENAME = "page_index.json"
_INDEX_VERSION = 1
_READ_PAGE_MAX_CHARS = 12_000
_SNIPPET_CHARS = 280
_EMBED_WEIGHT = 0.6

EmbedFn = Callable[[list[str]], "list[list[float]] | None"]

# Words that help find the right page for each authority amount.
DOC_TYPE_QUERIES: dict[str, str] = {
    "council": (
        "council rates certificate land information certificate annual rates and charges "
        "total payable levied this year general rates municipal charge waste charge "
        "fire services property levy instalments paid arrears balance outstanding"
    ),
    "water": (
        "water information statement quarterly service charges per day daily charge "
        "annual water service sewerage drainage parks charge total charges billing period"
    ),
    "oc": (
        "owners corporation certificate annual fees levies per annum quarterly instalment "
        "administrative fund maintenance fund budget section 151 insurance amount payable"
    ),
    "land_tax": (
        "state revenue office land tax certificate volume folio tax payable land tax assessed"
    ),
}

# General Section 32 topics, for stages that answer many questions at once.
SECTION32_PAGE_QUERY = (
    "section 32 vendor statement council rates water charges land tax owners corporation "
    "fees levies title volume folio registered proprietor planning zone overlay bushfire "
    "building permit insurance services connected easements covenants mortgage"
)

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was "
    "were will with what which who how does do any all".split()
)

_INDEX_LOCKS: dict[str, threading.Lock] = {}
_INDEX_LOCKS_GUARD = threading.Lock()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", (text or "").lower()) if t not in _STOPWORDS]


def _bm25_scores(query: str, page_texts: Sequence[str], *, k1: float = 1.5, b: float = 0.75) -> list[float]:
    query_terms = set(_tokens(query))
    if not query_terms or not page_texts:
        return [0.0] * len(page_texts)
    docs = [Counter(_tokens(text)) for text in page_texts]
    lengths = [sum(doc.values()) for doc in docs]
    avg_len = (sum(lengths) / len(lengths)) or 1.0
    n_docs = len(docs)
    scores: list[float] = []
    doc_freq = {term: sum(1 for doc in docs if term in doc) for term in query_terms}
    for doc, length in zip(docs, lengths):
        score = 0.0
        for term in query_terms:
            tf = doc.get(term, 0)
            if not tf:
                continue
            df = doc_freq[term]
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * length / avg_len))
        scores.append(score)
    return scores


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _normalise(scores: Sequence[float]) -> list[float]:
    top = max(scores, default=0.0)
    return [s / top if top > 0 else 0.0 for s in scores]


def _hybrid_scores(
    query: str,
    page_texts: Sequence[str],
    *,
    page_vectors: Sequence[Sequence[float] | None] | None = None,
    query_vector: Sequence[float] | None = None,
) -> list[float]:
    keyword = _normalise(_bm25_scores(query, page_texts))
    if not page_vectors or not query_vector:
        return keyword
    semantic = [max(0.0, _cosine(query_vector, vec)) if vec else 0.0 for vec in page_vectors]
    semantic = _normalise(semantic)
    return [_EMBED_WEIGHT * s + (1 - _EMBED_WEIGHT) * k for s, k in zip(semantic, keyword)]


def _snippet(text: str, query: str, length: int = _SNIPPET_CHARS) -> str:
    """The part of the page with the most query words, on one line."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= length:
        return flat
    terms = set(_tokens(query))
    best_start, best_hits = 0, -1
    step = max(40, length // 4)
    for start in range(0, max(1, len(flat) - length + 1), step):
        window = flat[start : start + length]
        hits = sum(1 for t in _tokens(window) if t in terms)
        if hits > best_hits:
            best_start, best_hits = start, hits
    snippet = flat[best_start : best_start + length]
    prefix = "…" if best_start > 0 else ""
    suffix = "…" if best_start + length < len(flat) else ""
    return f"{prefix}{snippet}{suffix}"


# ---------------------------------------------------------------------------
# In-memory selection, for extractors
# ---------------------------------------------------------------------------

def select_relevant_pages(
    pages: Sequence[tuple[int, str]],
    query: str,
    *,
    char_budget: int,
    embed_fn: EmbedFn | None = None,
    always_include_first: bool = False,
) -> list[tuple[int, str]]:
    """Pick the most relevant pages that fit in ``char_budget``, in page order.

    If every page fits, all pages are returned unchanged.
    """
    usable = [(number, text) for number, text in pages if (text or "").strip()]
    if sum(len(text) for _, text in usable) <= char_budget:
        return usable

    texts = [text for _, text in usable]
    page_vectors = query_vector = None
    if embed_fn is not None:
        try:
            vectors = embed_fn(texts + [query])
        except Exception as exc:  # embedding is best effort
            LOG.debug("page selection embeddings failed: %s", exc)
            vectors = None
        if vectors and len(vectors) == len(texts) + 1:
            page_vectors, query_vector = vectors[:-1], vectors[-1]

    scores = _hybrid_scores(query, texts, page_vectors=page_vectors, query_vector=query_vector)
    ranked = sorted(range(len(usable)), key=lambda i: scores[i], reverse=True)
    if always_include_first and usable:
        ranked = [0] + [i for i in ranked if i != 0]

    chosen: list[int] = []
    used = 0
    for index in ranked:
        length = len(texts[index])
        if used + length > char_budget:
            if not chosen:
                # The best page alone is too long; keep its most relevant part.
                chosen.append(index)
                used = char_budget
            continue
        chosen.append(index)
        used += length
    selected = [usable[i] for i in sorted(chosen)]
    if used >= char_budget and len(selected) == 1 and len(selected[0][1]) > char_budget:
        number, text = selected[0]
        selected = [(number, _best_window(text, query, char_budget))]
    return selected


def _best_window(text: str, query: str, size: int) -> str:
    if len(text) <= size:
        return text
    terms = set(_tokens(query))
    best_start, best_hits = 0, -1
    step = max(200, size // 6)
    for start in range(0, len(text) - size + 1, step):
        hits = sum(1 for t in _tokens(text[start : start + size]) if t in terms)
        if hits > best_hits:
            best_start, best_hits = start, hits
    return text[best_start : best_start + size]


def format_pages(selected: Sequence[tuple[int, str]], *, total_pages: int | None = None) -> str:
    total = f" of {total_pages}" if total_pages else ""
    return "\n\n".join(f"[Page {number}{total}]\n{text.strip()}" for number, text in selected)


def document_excerpt(doc: Any, query: str, char_budget: int, *, always_include_first: bool = True) -> str:
    """Relevant pages of a loaded Document, or its text when it already fits."""
    text = (getattr(doc, "raw_text", "") or getattr(doc, "normalized_text", "") or "").strip()
    pages = [(p.page_number, p.text or "") for p in (getattr(doc, "pages", None) or [])]
    if len(text) <= char_budget or not pages:
        return text[:char_budget]
    selected = select_relevant_pages(
        pages, query, char_budget=char_budget, always_include_first=always_include_first
    )
    total = (getattr(doc, "metadata", None) or {}).get("page_count") or len(pages)
    return format_pages(selected, total_pages=total)


# ---------------------------------------------------------------------------
# Persistent per-run index, for the assistant
# ---------------------------------------------------------------------------

def _run_lock(run_dir: Path) -> threading.Lock:
    key = str(run_dir.resolve())
    with _INDEX_LOCKS_GUARD:
        return _INDEX_LOCKS.setdefault(key, threading.Lock())


def _signature(doc_paths: Sequence[Path]) -> list[list[Any]]:
    sig = []
    for path in sorted(doc_paths, key=lambda p: p.name.lower()):
        try:
            stat = path.stat()
            sig.append([path.name, stat.st_size, int(stat.st_mtime)])
        except OSError:
            continue
    return sig


def _read_pdf_pages(path: Path) -> list[tuple[int, str]]:
    """Text of every page. Unlike the analysis loader, this never skips pages."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[tuple[int, str]] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            LOG.debug("page index: %s page %d unreadable: %s", path.name, number, exc)
            text = ""
        pages.append((number, text.strip()))
    return pages


def upload_pdfs(run_dir: Path) -> list[Path]:
    uploads = run_dir / "uploads"
    if not uploads.exists():
        return []
    return sorted(p for p in uploads.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def _index_path(run_dir: Path) -> Path:
    return run_dir / PAGE_INDEX_FILENAME


def load_page_index(run_dir: Path) -> dict[str, Any] | None:
    path = _index_path(run_dir)
    if not path.exists():
        return None
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if index.get("version") != _INDEX_VERSION:
        return None
    return index


def build_page_index(
    doc_paths: Sequence[Path],
    run_dir: Path,
    *,
    force: bool = False,
    embed: bool = True,
) -> dict[str, Any]:
    """Index every page of ``doc_paths``. Reuses the saved index when nothing changed."""
    signature = _signature(doc_paths)
    with _run_lock(run_dir):
        existing = load_page_index(run_dir)
        wants_vectors = embed and embeddings_available()
        if (
            existing
            and not force
            and existing.get("signature") == signature
            and (existing.get("embedding_model") or not wants_vectors)
        ):
            return existing

        pages: list[dict[str, Any]] = []
        for path in sorted(doc_paths, key=lambda p: p.name.lower()):
            try:
                file_pages = _read_pdf_pages(path)
            except Exception as exc:
                LOG.warning("page index: could not read %s: %s", path.name, exc)
                continue
            for number, text in file_pages:
                pages.append(
                    {"file": path.name, "page": number, "page_count": len(file_pages), "text": text}
                )

        vectors: list[list[float]] | None = None
        if wants_vectors:
            searchable = [i for i, page in enumerate(pages) if page["text"]]
            embedded = embed_texts([f"{pages[i]['file']}\n{pages[i]['text']}" for i in searchable])
            if embedded:
                vectors = [None] * len(pages)  # type: ignore[list-item]
                for i, vec in zip(searchable, embedded):
                    vectors[i] = vec

        index = {
            "version": _INDEX_VERSION,
            "signature": signature,
            "embedding_model": EMBEDDING_ID if vectors else None,
            "pages": pages,
            "vectors": vectors,
        }
        _index_path(run_dir).write_text(
            json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        LOG.info(
            "page index: %d pages from %d documents, embeddings=%s",
            len(pages), len(signature), bool(vectors),
        )
        return index


def ensure_page_index(run_dir: Path) -> dict[str, Any] | None:
    docs = upload_pdfs(run_dir)
    if not docs:
        return load_page_index(run_dir)
    return build_page_index(docs, run_dir)


def index_stats(run_dir: Path) -> dict[str, int]:
    index = load_page_index(run_dir)
    if not index:
        return {"pages": 0, "documents": 0}
    return {"pages": len(index.get("pages") or []), "documents": len(index.get("signature") or [])}


def _filename_boost(query: str, filename: str) -> float:
    stem = filename.lower().rsplit(".", 1)[0]
    file_terms = set(_tokens(stem.replace("_", " ").replace("-", " ")))
    query_terms = set(_tokens(query))
    if not file_terms or not query_terms:
        return 0.0
    return 0.15 * len(file_terms & query_terms) / len(file_terms)


def search_pages(
    run_dir: Path,
    query: str,
    *,
    top_k: int = 5,
    file: str | None = None,
) -> list[dict[str, Any]]:
    """The pages most likely to answer ``query``, best first."""
    index = ensure_page_index(run_dir)
    if not index:
        return []
    pages: list[dict[str, Any]] = index.get("pages") or []
    vectors = index.get("vectors") if index.get("embedding_model") == EMBEDDING_ID else None
    positions = [
        i for i, page in enumerate(pages)
        if page.get("text") and (not file or page["file"].lower() == file.lower())
    ]
    if not positions:
        return []

    texts = [pages[i]["text"] for i in positions]
    page_vectors = [vectors[i] for i in positions] if vectors else None
    query_vector = embed_text(query) if page_vectors else None
    scores = _hybrid_scores(query, texts, page_vectors=page_vectors, query_vector=query_vector)
    scored = [
        (score + _filename_boost(query, pages[i]["file"]), i)
        for score, i in zip(scores, positions)
    ]
    scored.sort(reverse=True)

    results = []
    for score, i in scored[: max(1, top_k)]:
        if score <= 0:
            break
        page = pages[i]
        results.append({
            "file": page["file"],
            "page": page["page"],
            "page_count": page.get("page_count"),
            "score": round(score, 4),
            "snippet": _snippet(page["text"], query),
        })
    return results


def read_page(run_dir: Path, file: str, page: int) -> dict[str, Any]:
    """Full text of one page."""
    index = ensure_page_index(run_dir)
    if not index:
        return {"error": "No documents are indexed for this matter."}
    wanted = (file or "").strip().lower()
    for entry in index.get("pages") or []:
        if entry["file"].lower() == wanted and int(entry["page"]) == int(page):
            text = entry.get("text") or ""
            truncated = len(text) > _READ_PAGE_MAX_CHARS
            return {
                "file": entry["file"],
                "page": entry["page"],
                "page_count": entry.get("page_count"),
                "text": text[:_READ_PAGE_MAX_CHARS] if text else "(No readable text on this page. It may be a scanned image.)",
                "truncated": truncated,
            }
    files = sorted({entry["file"] for entry in index.get("pages") or []})
    return {"error": f"Page {page} of '{file}' was not found.", "available_files": files}
