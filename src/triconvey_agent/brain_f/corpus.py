from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty
from typing import Any

from triconvey_agent.ai.openai_client import openai_runtime_disabled
from triconvey_agent.backend.runtime import ensure_runtime_dirs
from triconvey_agent.brain_f.cache import get_cached_pdf_analysis
from triconvey_agent.schemas.documents import Document


def build_document_corpus(
    doc_paths: list[Path],
    target_dir: Path,
    *,
    progress_callback=None,
    cache_only: bool = False,
) -> None:
    if _existing_corpus_is_current(doc_paths, target_dir):
        if progress_callback is not None:
            progress_callback("Using existing document corpus")
        return

    if _restore_shared_corpus(doc_paths, target_dir):
        if progress_callback is not None:
            progress_callback("Using shared document corpus")
        return

    shard_count = _desired_shard_count(len(doc_paths))
    shards = split_doc_paths(doc_paths, shard_count=shard_count)
    all_results: list[list[dict[str, Any]]] = []

    for shard_index, shard_paths in enumerate(shards, start=1):
        if progress_callback is not None:
            progress_callback(
                f"Corpus shard {shard_index}/{len(shards)}: {len(shard_paths)} document(s)"
            )
        shard_results = build_document_corpus_shard(
            shard_paths,
            progress_callback=progress_callback,
            shard_index=shard_index,
            shard_count=len(shards),
            cache_only=cache_only,
        )
        all_results.extend(shard_results)

    if progress_callback is not None:
        progress_callback("Finalising document corpus...")
    chunk_count = write_document_corpus(all_results, target_dir, doc_paths=doc_paths)
    if progress_callback is not None:
        progress_callback(f"Document corpus ready ({chunk_count} chunk(s))")


def split_doc_paths(doc_paths: list[Path], *, shard_count: int) -> list[list[Path]]:
    if not doc_paths:
        return []
    shard_count = max(1, min(shard_count, len(doc_paths)))
    return [doc_paths[index::shard_count] for index in range(shard_count)]


def build_document_corpus_shard(
    doc_paths: list[Path],
    *,
    progress_callback=None,
    shard_index: int | None = None,
    shard_count: int | None = None,
    cache_only: bool = False,
) -> list[list[dict[str, Any]]]:
    if not doc_paths:
        return []
    worker_limit = max(2, min(os.cpu_count() or 4, 8))
    worker_count = min(max(len(doc_paths), 1), worker_limit)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(
            executor.map(
                lambda doc_path: _build_corpus_entries_for_pdf_with_progress(
                    doc_path,
                    progress_callback=progress_callback,
                    shard_index=shard_index,
                    shard_count=shard_count,
                    cache_only=cache_only,
                ),
                doc_paths,
            )
        )


def write_document_corpus(
    results: list[list[dict[str, Any]]],
    target_dir: Path,
    *,
    doc_paths: list[Path],
) -> int:
    runtime = ensure_runtime_dirs()
    signature = _doc_paths_signature(doc_paths)
    corpus_dir = _shared_corpus_dir(runtime.temp_corpus_dir, signature)
    if corpus_dir.exists():
        _remove_tree(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict[str, Any]] = []
    lines: list[str] = []
    for doc_index, entries in enumerate(results):
        for chunk_index, chunk in enumerate(entries):
            chunk["chunk_id"] = f"{chunk['file']}:{chunk['page']}:{doc_index}:{chunk_index}"
            index.append(chunk)
            lines.append(f"FILE: {chunk['file']}")
            lines.append(f"PAGE: {chunk['page']}")
            lines.append(chunk["text"])
            lines.append("")

    precomputed_embeddings = False
    if _env_bool("CONVEY_PRECOMPUTE_CORPUS_EMBEDDINGS", False):
        _attach_chunk_embeddings(index)
        precomputed_embeddings = any(isinstance(item.get("embedding"), list) for item in index)

    corpus_text_path = corpus_dir / "document_corpus.txt"
    corpus_index_path = corpus_dir / "document_corpus_index.json"
    corpus_text_path.write_text("\n".join(lines).strip(), encoding="utf-8")
    corpus_index_path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _write_corpus_manifest(
        target_dir,
        corpus_path=corpus_text_path,
        index_path=corpus_index_path,
        precomputed_embeddings=precomputed_embeddings,
        source_signature=signature,
        document_count=len(doc_paths),
    )
    return len(index)


def build_corpus_entries_for_pdf(
    doc_path: Path,
    *,
    cache_only: bool = False,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], str]:
    effective_timeout = timeout_seconds
    if effective_timeout is None:
        effective_timeout = _pdf_timeout_seconds()

    if effective_timeout <= 0:
        return _build_corpus_entries_for_pdf_direct(doc_path, cache_only=cache_only)

    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_build_corpus_entries_worker,
        args=(str(doc_path), cache_only, queue),
    )
    process.start()

    try:
        payload = queue.get(timeout=effective_timeout)
    except Empty:
        process.terminate()
        process.join(timeout=1.0)
        return [], "timed_out"
    finally:
        queue.close()
        queue.join_thread()

    process.join(timeout=1.0)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)

    if not isinstance(payload, dict):
        return [], "failed"
    status = str(payload.get("status") or "failed")
    entries = payload.get("entries")
    if status == "ok" and isinstance(entries, list):
        return entries, "ok"
    return [], status


def _build_corpus_entries_for_pdf_with_progress(
    doc_path: Path,
    *,
    progress_callback=None,
    shard_index: int | None = None,
    shard_count: int | None = None,
    cache_only: bool = False,
) -> list[dict[str, Any]]:
    if progress_callback is not None:
        prefix = ""
        if shard_index is not None and shard_count is not None:
            prefix = f"[{shard_index}/{shard_count}] "
        progress_callback(f"{prefix}Reading {doc_path.name}")
    entries, status = build_corpus_entries_for_pdf(doc_path, cache_only=cache_only)
    if progress_callback is not None:
        prefix = ""
        if shard_index is not None and shard_count is not None:
            prefix = f"[{shard_index}/{shard_count}] "
        if status == "ok" and entries:
            progress_callback(f"{prefix}Done {doc_path.name} ({len(entries)} chunks)")
        elif status == "timed_out":
            progress_callback(f"{prefix}Timed out {doc_path.name}")
        else:
            reason = "cache miss or no text" if cache_only else "no text"
            progress_callback(f"{prefix}Skipped {doc_path.name} ({reason})")
    return entries


def build_corpus_entries_for_document(document: Document) -> list[dict[str, Any]]:
    analysis = {
        "pages": [
            {"page_number": page.page_number, "text": page.text or page.normalized_text or ""}
            for page in document.pages
        ],
    }
    return build_corpus_entries_from_analysis(document.filename, analysis)


def build_corpus_entries_from_analysis(filename: str, analysis: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for page in analysis.get("pages") or []:
        page_number = int(page.get("page_number") or 1)
        page_text = str(page.get("text") or "")
        if not page_text.strip():
            continue
        entries.extend(_chunk_page_text(filename, page_number, page_text))
    return entries


def _chunk_page_text(filename: str, page_number: int, page_text: str) -> list[dict[str, Any]]:
    sentences = _split_sentences(page_text)
    if not sentences:
        return []
    max_tokens = max(180, int(os.getenv("CONVEY_BRAIN_F_CHUNK_TOKENS", "320")))
    overlap_tokens = max(30, int(os.getenv("CONVEY_BRAIN_F_CHUNK_OVERLAP_TOKENS", "80")))
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_tokens = 0
    chunk_index = 0
    i = 0
    while i < len(sentences):
        sentence = sentences[i]
        sent_tokens = _rough_token_count(sentence)
        if current and current_tokens + sent_tokens > max_tokens:
            text = " ".join(current).strip()
            chunks.append(
                {
                    "chunk_id": f"{filename}:{page_number}:{chunk_index}",
                    "file": filename,
                    "page": page_number,
                    "text": text,
                    "token_count": current_tokens,
                }
            )
            chunk_index += 1
            current, current_tokens = _tail_overlap(current, overlap_tokens)
            continue
        current.append(sentence)
        current_tokens += sent_tokens
        i += 1
    if current:
        text = " ".join(current).strip()
        chunks.append(
            {
                "chunk_id": f"{filename}:{page_number}:{chunk_index}",
                "file": filename,
                "page": page_number,
                "text": f"Document: {filename}\nPage: {page_number}\n{text}",
                "token_count": current_tokens,
            }
        )
    return chunks


def _split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", cleaned)
    return [part.strip() for part in parts if part.strip()]


def _rough_token_count(text: str) -> int:
    return max(1, len(re.findall(r"\S+", text or "")))


def _tail_overlap(sentences: list[str], overlap_tokens: int) -> tuple[list[str], int]:
    kept: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        tokens = _rough_token_count(sentence)
        if kept and total + tokens > overlap_tokens:
            break
        kept.insert(0, sentence)
        total += tokens
    return kept, total


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _attach_chunk_embeddings(index: list[dict[str, Any]]) -> None:
    if openai_runtime_disabled():
        return
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not index:
        return
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = os.getenv("CONVEY_EMBEDDING_MODEL", "text-embedding-3-small")
        texts = [str(item.get("text") or "")[:4000] for item in index]
        batch_size = 64
        for start in range(0, len(index), batch_size):
            batch = texts[start:start + batch_size]
            response = client.embeddings.create(model=model, input=batch)
            for offset, datum in enumerate(response.data):
                index[start + offset]["embedding"] = list(datum.embedding)
    except Exception:
        return


def _remove_tree(root: Path) -> None:
    for child in sorted(root.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            child.rmdir()
    if root.exists():
        root.rmdir()


def _desired_shard_count(doc_count: int) -> int:
    configured = os.getenv("CONVEY_BRAIN_F_CORPUS_SHARDS")
    if configured:
        try:
            return max(1, min(int(configured), max(1, doc_count)))
        except ValueError:
            pass
    if doc_count <= 2:
        return 1
    if doc_count <= 6:
        return 2
    if doc_count <= 12:
        return 3
    return 4


def _existing_corpus_is_current(doc_paths: list[Path], target_dir: Path) -> bool:
    manifest_path = target_dir / "document_corpus_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    corpus_path = Path(str(manifest.get("corpus_path") or ""))
    index_path = Path(str(manifest.get("index_path") or ""))
    if not corpus_path.exists() or not index_path.exists():
        return False

    expected_signature = _doc_paths_signature(doc_paths)
    return str(manifest.get("source_signature") or "") == expected_signature


def _restore_shared_corpus(doc_paths: list[Path], target_dir: Path) -> bool:
    runtime = ensure_runtime_dirs()
    signature = _doc_paths_signature(doc_paths)
    corpus_dir = _shared_corpus_dir(runtime.temp_corpus_dir, signature)
    corpus_path = corpus_dir / "document_corpus.txt"
    index_path = corpus_dir / "document_corpus_index.json"
    if not corpus_path.exists() or not index_path.exists():
        return False
    _write_corpus_manifest(
        target_dir,
        corpus_path=corpus_path,
        index_path=index_path,
        precomputed_embeddings=False,
        source_signature=signature,
        document_count=len(doc_paths),
    )
    return True


def _doc_paths_signature(doc_paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(doc_paths, key=lambda item: item.name.lower()):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(path.name.lower().encode("utf-8", errors="ignore"))
        digest.update(str(int(stat.st_size)).encode("ascii"))
        digest.update(str(int(stat.st_mtime_ns)).encode("ascii"))
    return digest.hexdigest()


def _uploaded_pdf_paths(target_dir: Path) -> list[Path]:
    uploads_dir = target_dir / "uploads"
    if not uploads_dir.exists():
        return []
    return sorted(
        path for path in uploads_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def _shared_corpus_dir(temp_corpus_dir: Path, source_signature: str) -> Path:
    return temp_corpus_dir / "_shared" / source_signature


def _write_corpus_manifest(
    target_dir: Path,
    *,
    corpus_path: Path,
    index_path: Path,
    precomputed_embeddings: bool,
    source_signature: str,
    document_count: int,
) -> None:
    (target_dir / "document_corpus_manifest.json").write_text(
        json.dumps(
            {
                "corpus_path": str(corpus_path),
                "index_path": str(index_path),
                "expires_after_hours": 24,
                "precomputed_embeddings": precomputed_embeddings,
                "source_signature": source_signature,
                "document_count": document_count,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _build_corpus_entries_worker(doc_path: str, cache_only: bool, queue) -> None:
    path = Path(doc_path)
    entries, status = _build_corpus_entries_for_pdf_direct(path, cache_only=cache_only)
    queue.put({"status": status, "entries": entries})


def _build_corpus_entries_for_pdf_direct(
    doc_path: Path,
    *,
    cache_only: bool,
) -> tuple[list[dict[str, Any]], str]:
    try:
        analysis = get_cached_pdf_analysis(doc_path, allow_build=not cache_only)
    except FileNotFoundError:
        return [], "cache_miss"
    except Exception:
        return [], "failed"
    return build_corpus_entries_from_analysis(doc_path.name, analysis), "ok"


def _pdf_timeout_seconds() -> float:
    raw = os.getenv("CONVEY_BRAIN_F_PDF_TIMEOUT_SECONDS", "8")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 8.0
