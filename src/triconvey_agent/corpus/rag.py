"""Corpus RAG (Retrieval-Augmented Generation) index.

Builds a semantic search index over document chunks so Brain F can retrieve
the most relevant text passages for any question.

Storage: {run_dir}/rag_index.json
  {
    "chunks": [{"text": str, "file": str, "page": int, "heading": str}],
    "embeddings": [[float, ...], ...]   # null if embedding is not available
  }

Embedding: OpenAI text-embedding-3-small (falls back to TF-IDF keyword matching
when no OpenAI key is available or the API call fails).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

from triconvey_agent.ai.openai_client import openai_runtime_disabled

LOG = logging.getLogger(__name__)

_EMBED_MODEL = "text-embedding-3-small"
_CHUNK_SIZE = 400       # characters per chunk
_CHUNK_OVERLAP = 80     # overlap between consecutive chunks
_TOP_K_DEFAULT = 6


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, filename: str, page_hint: int = 0) -> list[dict[str, Any]]:
    """Split text into overlapping chunks, preserving heading context."""
    chunks: list[dict[str, Any]] = []
    heading = ""
    lines = text.splitlines()
    # Build heading-aware paragraphs
    para = []
    paragraphs: list[tuple[str, str]] = []  # (heading, text)
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if para:
                paragraphs.append((heading, " ".join(para)))
                para = []
        else:
            # Detect heading: short lines in UPPER CASE or ending with ":"
            if len(stripped) < 80 and (stripped.isupper() or stripped.endswith(":")):
                if para:
                    paragraphs.append((heading, " ".join(para)))
                    para = []
                heading = stripped
            else:
                para.append(stripped)
    if para:
        paragraphs.append((heading, " ".join(para)))

    for h, body in paragraphs:
        # Slide a window over the body
        start = 0
        while start < len(body):
            end = start + _CHUNK_SIZE
            chunk_text = body[start:end].strip()
            if chunk_text:
                chunks.append({
                    "text": chunk_text,
                    "file": filename,
                    "page": page_hint,
                    "heading": h,
                })
            start += _CHUNK_SIZE - _CHUNK_OVERLAP

    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed texts using OpenAI API. Returns None on failure."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or openai_runtime_disabled():
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        response = client.embeddings.create(
            model=_EMBED_MODEL,
            input=texts,
            encoding_format="float",
        )
        return [item.embedding for item in response.data]
    except Exception as exc:
        LOG.warning("RAG embedding failed: %s — falling back to keyword search", exc)
        return None


def _embed_single(text: str) -> list[float] | None:
    result = _embed_texts([text])
    return result[0] if result else None


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# TF-IDF keyword fallback
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _keyword_score(query_tokens: set[str], chunk_text: str) -> float:
    chunk_tokens = _tokenize(chunk_text)
    if not chunk_tokens:
        return 0.0
    overlap = len(query_tokens & chunk_tokens)
    return overlap / math.sqrt(len(chunk_tokens))


# ---------------------------------------------------------------------------
# Build index
# ---------------------------------------------------------------------------

def build_rag_index(
    doc_paths: list[Path],
    run_dir: Path,
    *,
    force: bool = False,
) -> None:
    """Build RAG index for all documents in the run.

    If the index already exists and force=False, skip rebuilding.
    """
    index_path = run_dir / "rag_index.json"
    if index_path.exists() and not force:
        return

    from triconvey_agent.ingest.pdf_loader import load_pdf_document

    all_chunks: list[dict[str, Any]] = []

    for doc_path in doc_paths:
        try:
            pdf = load_pdf_document(doc_path)
            # Extract per-page text for better page tracking
            if hasattr(pdf, "pages") and pdf.pages:
                for page_num, page in enumerate(pdf.pages, start=1):
                    page_text = page.text if hasattr(page, "text") else str(page)
                    if page_text:
                        chunks = _chunk_text(page_text, doc_path.name, page_num)
                        all_chunks.extend(chunks)
            else:
                full_text = pdf.full_text if hasattr(pdf, "full_text") else ""
                if full_text:
                    all_chunks.extend(_chunk_text(full_text, doc_path.name))
        except Exception as exc:
            LOG.warning("RAG: could not process %s: %s", doc_path.name, exc)

    if not all_chunks:
        LOG.warning("RAG: no chunks extracted, skipping index build")
        return

    # Embed all chunks (batch to stay under API limits)
    embeddings: list[list[float] | None] = []
    batch_size = 100
    texts = [c["text"] for c in all_chunks]
    embed_available = bool(os.environ.get("OPENAI_API_KEY"))

    if embed_available:
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_embeddings = _embed_texts(batch)
            if batch_embeddings:
                embeddings.extend(batch_embeddings)
            else:
                embeddings.extend([None] * len(batch))
                embed_available = False  # stop trying
    else:
        embeddings = [None] * len(all_chunks)

    index = {
        "chunks": all_chunks,
        "embeddings": embeddings,
        "has_embeddings": embed_available and any(e is not None for e in embeddings),
    }
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    LOG.info("RAG index built: %d chunks, embeddings=%s", len(all_chunks), embed_available)


def append_to_rag_index(doc_path: Path, run_dir: Path) -> None:
    """Add a single document's chunks to an existing RAG index."""
    index_path = run_dir / "rag_index.json"
    existing: dict[str, Any] = {"chunks": [], "embeddings": [], "has_embeddings": False}
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    from triconvey_agent.ingest.pdf_loader import load_pdf_document
    new_chunks: list[dict[str, Any]] = []
    try:
        pdf = load_pdf_document(doc_path)
        if hasattr(pdf, "pages") and pdf.pages:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = page.text if hasattr(page, "text") else str(page)
                if page_text:
                    new_chunks.extend(_chunk_text(page_text, doc_path.name, page_num))
        else:
            full_text = pdf.full_text if hasattr(pdf, "full_text") else ""
            if full_text:
                new_chunks.extend(_chunk_text(full_text, doc_path.name))
    except Exception as exc:
        LOG.warning("RAG append: could not process %s: %s", doc_path.name, exc)
        return

    if not new_chunks:
        return

    new_embeddings: list[list[float] | None] = []
    if os.environ.get("OPENAI_API_KEY"):
        result = _embed_texts([c["text"] for c in new_chunks])
        new_embeddings = result if result else [None] * len(new_chunks)
    else:
        new_embeddings = [None] * len(new_chunks)

    existing["chunks"].extend(new_chunks)
    existing["embeddings"].extend(new_embeddings)
    existing["has_embeddings"] = any(e is not None for e in existing["embeddings"])
    index_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------

def retrieve(query: str, run_dir: Path, top_k: int = _TOP_K_DEFAULT) -> list[dict[str, Any]]:
    """Retrieve the most relevant chunks for a query.

    Returns list of {text, file, page, heading, score} sorted by relevance.
    """
    index_path = run_dir / "rag_index.json"
    if not index_path.exists():
        return []

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    chunks: list[dict[str, Any]] = index.get("chunks", [])
    embeddings: list[Any] = index.get("embeddings", [])
    has_embeddings: bool = index.get("has_embeddings", False)

    if not chunks:
        return []

    scores: list[float] = []

    if has_embeddings and any(e is not None for e in embeddings):
        query_emb = _embed_single(query)
        if query_emb:
            for emb in embeddings:
                if emb is not None:
                    scores.append(_cosine(query_emb, emb))
                else:
                    scores.append(0.0)
        else:
            # Fallback to keyword
            query_tokens = _tokenize(query)
            scores = [_keyword_score(query_tokens, c["text"]) for c in chunks]
    else:
        query_tokens = _tokenize(query)
        scores = [_keyword_score(query_tokens, c["text"]) for c in chunks]

    ranked = sorted(
        [{"chunk": c, "score": s} for c, s in zip(chunks, scores) if s > 0],
        key=lambda x: x["score"],
        reverse=True,
    )

    results = []
    for item in ranked[:top_k]:
        chunk = item["chunk"]
        results.append({
            "text": chunk["text"],
            "file": chunk["file"],
            "page": chunk.get("page"),
            "heading": chunk.get("heading", ""),
            "score": round(item["score"], 4),
        })
    return results
