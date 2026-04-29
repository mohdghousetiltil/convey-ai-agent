"""Incremental single-document processor.

When a new document is uploaded via the matter agent chat:
1. Validate relevance
2. Extract facts from that document only
3. Run questions whose fact paths are populated by this document type
4. Compare against existing answers — find what changed
5. Return proposed changes for user approval
6. On approval, merge into the run

This keeps chat-doc processing fast (single doc, not full re-run) and gives
the user explicit control over what gets written into the matter.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

# Map document types to the fact path prefixes they populate.
# Brain D will only re-run questions whose fact_paths overlap with these.
_DOC_TYPE_FACT_PREFIXES: dict[str, list[str]] = {
    "building_approval":    ["building_approval", "building"],
    "owners_corporation":   ["rates.owners_corporation", "oc"],
    "council_rates":        ["rates.council"],
    "water_authority":      ["rates.water"],
    "planning_certificate": ["planning", "zone", "overlay", "bushfire"],
    "vic_title":            ["title"],
    "land_tax":             ["rates.state_revenue", "rates.land_tax"],
    "vendor_form":          ["vendor", "services", "title", "planning"],
    "contract_of_sale":     ["contract"],
    "general":              [],
}


def process_single_document(
    doc_path: Path,
    run_dir: Path,
    *,
    model: str = "gpt-4.1-mini",
    provider: str = "openai",
    run_id: str = "",
    matter_id: str = "",
) -> dict[str, Any]:
    """Process a single newly uploaded document.

    Returns:
    {
        "validation": {is_valid, is_suspicious, document_type_guess, warning_message},
        "corpus_entry": {...},          # extracted structured data
        "proposed_changes": [           # answers that would change
            {
                "question_id": str,
                "question_label": str,
                "current_value": Any,
                "proposed_value": Any,
                "confidence": float,
                "source": str,          # filename + page
                "ui_section": str,      # which tab/section this affects
            }
        ],
        "document_id": str,
        "filename": str,
    }
    """
    from uuid import uuid4
    from triconvey_agent.corpus.document_validator import validate_document_quick
    from triconvey_agent.corpus.extractor import extract_corpus_entry
    from triconvey_agent.corpus.rag import append_to_rag_index
    from triconvey_agent.ingest.pdf_loader import load_pdf_document
    from triconvey_agent.backend.service import _make_ai_client_for_corpus, _provider_from_model

    document_id = str(uuid4())
    filename = doc_path.name

    # 1. Load PDF text
    try:
        pdf = load_pdf_document(doc_path)
        full_text = pdf.full_text if hasattr(pdf, "full_text") else _pdf_text(doc_path)
        page_count = pdf.page_count if hasattr(pdf, "page_count") else 0
    except Exception as exc:
        return {
            "error": f"Could not read PDF: {exc}",
            "document_id": document_id,
            "filename": filename,
        }

    # 2. Quick validation (keyword-based, instant)
    validation = validate_document_quick(filename, full_text)
    doc_type = validation["document_type_guess"]

    # 3. AI corpus extraction (rich structured data)
    resolved_provider = _provider_from_model(model)
    ai_client = _make_ai_client_for_corpus(resolved_provider, model)
    try:
        corpus_entry = extract_corpus_entry(
            document_id=document_id,
            filename=filename,
            document_type=doc_type,
            full_text=full_text,
            page_count=page_count,
            ai_client=ai_client,
            model=model,
            provider=resolved_provider,
        )
    except Exception as exc:
        LOG.warning("Corpus extraction failed for %s: %s", filename, exc)
        corpus_entry = None

    # 4. Find proposed changes by running Brain D for this document only
    proposed_changes = _find_proposed_changes(
        doc_path=doc_path,
        doc_type=doc_type,
        run_dir=run_dir,
        corpus_entry=corpus_entry,
    )

    # 5. Append to RAG index (so future queries can search this document)
    try:
        append_to_rag_index(doc_path, run_dir)
    except Exception as exc:
        LOG.warning("RAG append failed for %s: %s", filename, exc)

    return {
        "validation": validation,
        "corpus_entry": corpus_entry.to_dict() if corpus_entry else None,
        "proposed_changes": proposed_changes,
        "document_id": document_id,
        "filename": filename,
    }


def apply_document_changes(
    run_dir: Path,
    document_id: str,
    doc_path: Path,
    corpus_entry_dict: dict[str, Any] | None,
    approved_change_ids: list[str],
    all_changes: list[dict[str, Any]],
    matter_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Apply approved changes from a single-document processing result.

    - Writes approved answer patches to answers.json
    - Confirms the corpus entry (moves from pending to confirmed)
    - Copies the document to the run's uploads folder if not already there
    """
    from triconvey_agent.corpus.builder import add_pending, confirm_document, load_corpus, save_corpus
    from triconvey_agent.corpus.schema import DocumentCorpusEntry

    results: dict[str, Any] = {"applied_changes": [], "corpus_confirmed": False}

    # Apply approved answer patches
    answers_path = run_dir / "answers.json"
    if answers_path.exists():
        answers = json.loads(answers_path.read_text(encoding="utf-8"))
        approved_set = set(approved_change_ids)
        patched_count = 0
        for change in all_changes:
            qid = change.get("question_id")
            if qid and qid in approved_set:
                # Patch this answer
                if isinstance(answers, list):
                    for ans in answers:
                        if ans.get("question_id") == qid:
                            ans["value_json"] = change.get("proposed_value")
                            ans["human_edited"] = True
                            ans["human_value_json"] = change.get("proposed_value")
                            patched_count += 1
                            results["applied_changes"].append(qid)
                elif isinstance(answers, dict):
                    if qid in answers:
                        answers[qid]["value_json"] = change.get("proposed_value")
                        answers[qid]["human_edited"] = True
                        patched_count += 1
                        results["applied_changes"].append(qid)
        answers_path.write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
        results["patched_answers"] = patched_count

    # Confirm corpus entry
    if corpus_entry_dict and document_id:
        try:
            corpus = load_corpus(run_dir, matter_id=matter_id, run_id=run_id)
            entry = DocumentCorpusEntry.from_dict(corpus_entry_dict)
            add_pending(corpus, entry, run_dir)
            confirmed = confirm_document(corpus, document_id, run_dir)
            results["corpus_confirmed"] = confirmed is not None
        except Exception as exc:
            LOG.warning("Corpus confirm failed: %s", exc)

    # Copy document to uploads folder
    uploads_dir = run_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / doc_path.name
    if not dest.exists() and doc_path.exists():
        import shutil
        shutil.copy2(doc_path, dest)
        results["document_saved"] = str(dest)

    return results


# ---------------------------------------------------------------------------
# Internal: find proposed changes
# ---------------------------------------------------------------------------

def _find_proposed_changes(
    doc_path: Path,
    doc_type: str,
    run_dir: Path,
    corpus_entry: Any | None,
) -> list[dict[str, Any]]:
    """Run extractors for this single document type and compare against existing answers."""
    from triconvey_agent.canonical.runner.fact_extraction import run_all_extractors
    from triconvey_agent.canonical.facts.store import FactStoreImpl
    from triconvey_agent.canonical.questions.loader import load_question_registry
    from triconvey_agent.canonical.router import answer_all_questions
    from triconvey_agent.ingest.pdf_loader import load_pdf_document

    try:
        pdf = load_pdf_document(doc_path)
    except Exception:
        return []

    # Extract facts from just this document
    single_store = FactStoreImpl()
    try:
        facts = run_all_extractors(pdf)
        for fact in facts:
            single_store.add(fact)
    except Exception as exc:
        LOG.warning("Single-doc fact extraction failed: %s", exc)
        return []

    # Load existing answers
    answers_path = run_dir / "answers.json"
    if not answers_path.exists():
        return []

    existing_answers: dict[str, Any] = {}
    try:
        raw = json.loads(answers_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            existing_answers = {a["question_id"]: a for a in raw if "question_id" in a}
        elif isinstance(raw, dict):
            existing_answers = raw
    except Exception:
        return []

    # Relevant fact prefixes for this doc type
    relevant_prefixes = _DOC_TYPE_FACT_PREFIXES.get(doc_type, [])

    # Only run questions whose fact paths overlap with this doc's prefixes
    registry = load_question_registry()
    relevant_questions = []
    for q in registry.values():
        if not relevant_prefixes:
            break
        fact_paths = q.fact_paths or []
        if any(any(fp.startswith(prefix) for prefix in relevant_prefixes) for fp in fact_paths):
            relevant_questions.append(q)

    if not relevant_questions:
        # Fall back to corpus entry fields if no Brain D questions match
        return _corpus_entry_to_changes(corpus_entry, existing_answers)

    # Run Brain D for relevant questions only
    new_answers = answer_all_questions(relevant_questions, single_store, ai_client=None)

    # answer_all_questions() returns a dict[qid, AnswerObject] in the main pipeline.
    # Older incremental code assumed a list-like output; normalize here.
    if isinstance(new_answers, dict):
        new_answer_items = list(new_answers.values())
    else:
        new_answer_items = list(new_answers)

    # Build proposed changes list
    proposed: list[dict[str, Any]] = []
    for ans in new_answer_items:
        qid = getattr(ans, "question_id", None)
        if not qid:
            continue
        new_val = getattr(ans, "value", None)
        if new_val is None:
            continue
        existing = existing_answers.get(qid)
        current_val = None
        if existing:
            if isinstance(existing, dict):
                current_val = (
                    existing.get("human_value_json")
                    or existing.get("value_json")
                    or existing.get("human_value")
                    or existing.get("value")
                )
        # Only propose if it's new or different
        if current_val != new_val:
            sources = [s.file for s in (ans.evidence or [])] if hasattr(ans, "evidence") and ans.evidence else [doc_path.name]
            proposed.append({
                "question_id": qid,
                "question_label": getattr(ans, "label", qid),
                "current_value": current_val,
                "proposed_value": new_val,
                "confidence": round(getattr(ans, "confidence", 0.8), 3),
                "source": ", ".join(set(sources)),
                "ui_section": getattr(ans, "tab", ""),
            })

    return proposed


def _corpus_entry_to_changes(
    entry: Any | None,
    existing_answers: dict[str, Any],
) -> list[dict[str, Any]]:
    """Derive proposed changes purely from corpus entry when Brain D has no matches."""
    if not entry:
        return []
    changes = []
    field_map = [
        ("building_permit_number", entry.building_permit.number if entry.building_permit else None),
        ("building_permit_date", entry.building_permit.date if entry.building_permit else None),
        ("occupancy_permit_number", entry.occupancy_permit.number if entry.occupancy_permit else None),
        ("council_name", entry.council_authority.name if entry.council_authority else None),
        ("water_authority_name", entry.water_authority.name if entry.water_authority else None),
        ("is_bushfire_prone", entry.is_bushfire_prone),
    ]
    for qid, val in field_map:
        if val is None:
            continue
        existing = existing_answers.get(qid, {})
        current = existing.get("human_value_json") or existing.get("value_json")
        if current != val:
            changes.append({
                "question_id": qid,
                "question_label": qid.replace("_", " ").title(),
                "current_value": current,
                "proposed_value": val,
                "confidence": 0.85,
                "source": entry.filename,
                "ui_section": "",
            })
    return changes


def _pdf_text(path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages[:20])
    except Exception:
        return ""
