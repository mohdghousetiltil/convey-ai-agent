"""Document memory — per-doc structured summaries stored at upload/run time.

A DocumentMemory record captures what each uploaded PDF is about so that
Brain F can reason about which documents to consult without re-reading them
every turn.

Layout on disk:
  {run_dir}/document_memory.json
    {
      "filename.pdf": {
        "filename": "filename.pdf",
        "doc_type": "Water Authority Certificate",
        "authority_name": "Greater Western Water",
        "dates_found": ["2025-07-01", "2026-06-30"],
        "amounts_found": ["$737.01"],
        "key_facts": ["Annual water charge: $737.01", "..."],
        "page_count": 3
      },
      ...
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from triconvey_agent.brain_f.cache import get_cached_pdf_analysis
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.schemas import Fact


_DOC_TYPE_HINTS: dict[str, str] = {
    "land information certificate": "Council Rates Certificate",
    "council rates": "Council Rates Certificate / Notice",
    "water authority": "Water Authority Certificate",
    "gww": "Water Authority Certificate",
    "south east water": "Water Authority Certificate",
    "yarra valley water": "Water Authority Certificate",
    "owners corporation": "Owners Corporation Certificate",
    "oc certificate": "Owners Corporation Certificate",
    "planning certificate": "Planning Certificate",
    "section 32": "Vendor's Statement (Section 32)",
    "vendor statement": "Vendor's Statement (Section 32)",
    "certificate of title": "Certificate of Title",
    "land victoria": "Certificate of Title",
    "land tax": "Land Tax Certificate",
    "building approval": "Building Permit/Approval",
}


def _infer_doc_type(filename: str, raw_text: str) -> str:
    combined = (filename + " " + (raw_text or "")[:500]).lower()
    for keyword, label in _DOC_TYPE_HINTS.items():
        if keyword in combined:
            return label
    return "Unknown Document"


def _extract_amounts(text: str) -> list[str]:
    import re
    amounts = re.findall(r"\$[\d,]+\.\d{2}", text or "")
    # Deduplicate, preserving order, return top 10
    seen: set[str] = set()
    result: list[str] = []
    for a in amounts:
        if a not in seen:
            seen.add(a)
            result.append(a)
        if len(result) >= 10:
            break
    return result


def _extract_dates(text: str) -> list[str]:
    import re
    dates = re.findall(
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}[-/]\d{2}[-/]\d{2})\b",
        text or "",
    )
    seen: set[str] = set()
    result: list[str] = []
    for d in dates:
        if d not in seen:
            seen.add(d)
            result.append(d)
        if len(result) >= 6:
            break
    return result


def _facts_for_file(filename: str, store: FactStoreImpl) -> list[Fact]:
    results: list[Fact] = []
    for path in store.paths():
        for fact in store.get_all(path):
            sources = fact.sources or []
            if any(s.file == filename for s in sources):
                results.append(fact)
    return results


def build_document_memory(
    doc_paths: list[Path],
    store: FactStoreImpl,
    run_dir: Path,
    *,
    progress_callback=None,
) -> dict[str, Any]:
    """Build and persist document memory for all uploaded docs.

    Returns the memory dict (also written to disk).
    """
    memory: dict[str, Any] = {}

    for doc_path in doc_paths:
        filename = doc_path.name
        if progress_callback is not None:
            progress_callback(f"Memory: reading {filename}")
        try:
            analysis = get_cached_pdf_analysis(doc_path)
            raw_text = str(analysis.get("raw_text") or "")
            page_count = analysis.get("page_count")
        except Exception:
            raw_text = ""
            page_count = None

        doc_type = _infer_doc_type(filename, raw_text)
        amounts = _extract_amounts(raw_text)
        dates = _extract_dates(raw_text)

        # Pull key facts that came from this document
        doc_facts = _facts_for_file(filename, store)
        key_facts: list[str] = []
        for fact in doc_facts:
            if fact.value is not None and str(fact.value).strip():
                key_facts.append(f"{fact.path}: {fact.value} (conf {fact.confidence:.2f})")

        # Try to find the authority name
        authority_name: str | None = None
        for fact in doc_facts:
            if "authority_name" in fact.path or "authority" in fact.path:
                if fact.value and str(fact.value).strip():
                    authority_name = str(fact.value)
                    break

        memory[filename] = {
            "filename": filename,
            "doc_type": doc_type,
            "authority_name": authority_name,
            "dates_found": dates,
            "amounts_found": amounts,
            "key_facts": key_facts[:20],
            "page_count": page_count,
        }

    out_path = run_dir / "document_memory.json"
    out_path.write_text(json.dumps(memory, indent=2, default=str), encoding="utf-8")
    return memory


def load_document_memory(run_dir: Path) -> dict[str, Any]:
    """Load document memory from disk. Returns empty dict if not found."""
    path = run_dir / "document_memory.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def format_memory_for_prompt(memory: dict[str, Any]) -> str:
    """Render document memory as a concise prompt block."""
    if not memory:
        return "No document memory available."
    lines: list[str] = ["## Uploaded documents summary:"]
    for filename, info in memory.items():
        lines.append(f"\n### {filename}")
        lines.append(f"Type: {info.get('doc_type', 'Unknown')}")
        if info.get("authority_name"):
            lines.append(f"Authority: {info['authority_name']}")
        if info.get("amounts_found"):
            lines.append(f"Amounts in document: {', '.join(info['amounts_found'][:5])}")
        if info.get("key_facts"):
            lines.append("Key extracted facts:")
            for kf in info["key_facts"][:8]:
                lines.append(f"  - {kf}")
    return "\n".join(lines)
