from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from triconvey_agent.backend.runtime import ensure_runtime_dirs
from triconvey_agent.ingest.pdf_loader import load_pdf_document
from triconvey_agent.schemas.documents import Document


def get_brain_f_pdf_cache_dir() -> Path:
    runtime = ensure_runtime_dirs()
    cache_dir = runtime.cache_dir / "brain_f_pdf"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _pdf_cache_key(path: str | Path) -> str:
    doc_path = Path(path).expanduser().resolve()
    stat = doc_path.stat()
    digest = hashlib.sha256()
    digest.update(str(doc_path).encode("utf-8", errors="ignore"))
    digest.update(str(stat.st_size).encode("ascii"))
    digest.update(str(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))).encode("ascii"))
    return digest.hexdigest()


def _cache_path_for(path: str | Path) -> Path:
    return get_brain_f_pdf_cache_dir() / f"{_pdf_cache_key(path)}.json"


def _payload_from_document(doc_path: Path, document: Document) -> dict[str, Any]:
    page_entries = _clean_document_pages(document)
    return {
        "cache_key": _pdf_cache_key(doc_path),
        "filename": doc_path.name,
        "raw_text": document.raw_text or document.normalized_text or "",
        "page_count": document.metadata.get("page_count") or len(page_entries) or None,
        "pages": [
            {"page_number": page_number, "text": page_text}
            for page_number, page_text in page_entries
        ],
    }


def prime_cached_pdf_analysis(path: str | Path, document: Document) -> dict[str, Any]:
    doc_path = Path(path).expanduser().resolve()
    payload = _payload_from_document(doc_path, document)
    _cache_path_for(doc_path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def get_cached_pdf_analysis(path: str | Path, *, allow_build: bool = True) -> dict[str, Any]:
    doc_path = Path(path).expanduser().resolve()
    cache_path = _cache_path_for(doc_path)
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

    if not allow_build:
        raise FileNotFoundError(f"Brain F cache not available for {doc_path.name}")

    document = load_pdf_document(doc_path)
    payload = _payload_from_document(doc_path, document)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def _clean_document_pages(doc) -> list[tuple[int, str]]:
    pages = doc.pages or []
    if not pages:
        return [(1, (doc.raw_text or doc.normalized_text or "").strip())]

    repeated_head: dict[str, int] = {}
    repeated_foot: dict[str, int] = {}
    if len(pages) >= 2:
        for page in pages:
            lines = [line.strip() for line in (page.text or page.normalized_text or "").splitlines() if line.strip()]
            for line in lines[:3]:
                repeated_head[line] = repeated_head.get(line, 0) + 1
            for line in lines[-3:]:
                repeated_foot[line] = repeated_foot.get(line, 0) + 1

    header_footer = {
        line
        for line, count in {**repeated_head, **repeated_foot}.items()
        if count >= 2 and len(line) <= 120
    }

    cleaned: list[tuple[int, str]] = []
    for page in pages:
        lines = [line.strip() for line in (page.text or page.normalized_text or "").splitlines() if line.strip()]
        filtered = [line for line in lines if line not in header_footer]
        cleaned.append((page.page_number, "\n".join(filtered).strip()))
    return cleaned
