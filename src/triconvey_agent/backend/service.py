from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from triconvey_agent.ai.openai_client import OpenAIResponsesClient, openai_runtime_disabled
from triconvey_agent.ai.openrouter_client import OpenRouterClient
from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths
from triconvey_agent.backend.settings import load_local_settings
from triconvey_agent.brain_f.corpus import build_document_corpus
from triconvey_agent.brain_f.memory import build_document_memory, load_document_memory
from triconvey_agent.canonical.brain_d import build_action_plan
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.questions.loader import load_question_registry
from triconvey_agent.canonical.router import answer_all_questions
from triconvey_agent.canonical.runner.ai_review import run_ai_review
from triconvey_agent.canonical.runner.fact_extraction import extract_fact_store
from triconvey_agent.canonical.runner.summary_writer import write_summary
from triconvey_agent.canonical.schemas import AnswerObject, FormActionPlan, FactStore

LOG = logging.getLogger(__name__)
_BRAIN_F_BUILD_LOCK = threading.Lock()
_BRAIN_F_BUILD_EVENTS: dict[str, threading.Event] = {}
_AUTOFILL_ACTIVITY_LOCK = threading.Lock()
_ACTIVE_AUTOFILL_RUNS: set[str] = set()
_BRAIN_F_DEFAULT_WARMUP_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("CONVEY_BRAIN_F_WARMUP_DELAY_SECONDS", "0")),
)

_AI_MODE_CHOICES = {"cost_efficient", "all_time_best", "turbo"}
_OPENAI_MODELS = ("gpt-4.1-mini", "gpt-4.1", "gpt-4o")
_ANTHROPIC_MODELS = ("claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-7")
_GOOGLE_MODELS = ("gemini-2.5-flash", "gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", "gemini-1.5-pro")
_AI_COLLABORATION_PRESETS: dict[str, dict[str, list[dict[str, str]]]] = {
    "openai": {
        "cost_efficient": [
            {"provider": "openai", "model": "gpt-4.1-mini", "role": "Scout"},
            {"provider": "openai", "model": "gpt-4.1", "role": "Reviewer"},
        ],
        "all_time_best": [
            {"provider": "openai", "model": "gpt-4.1", "role": "Lead"},
            {"provider": "openai", "model": "gpt-4o", "role": "Cross-check"},
        ],
        "turbo": [
            {"provider": "openai", "model": "gpt-4o", "role": "Turbo"},
            {"provider": "openai", "model": "gpt-4.1-mini", "role": "Verifier"},
        ],
    },
    "anthropic": {
        "cost_efficient": [
            {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "role": "Scout"},
            {"provider": "anthropic", "model": "claude-sonnet-4-6", "role": "Reviewer"},
        ],
        "all_time_best": [
            {"provider": "anthropic", "model": "claude-opus-4-7", "role": "Lead"},
            {"provider": "anthropic", "model": "claude-sonnet-4-6", "role": "Cross-check"},
        ],
        "turbo": [
            {"provider": "anthropic", "model": "claude-sonnet-4-6", "role": "Turbo"},
            {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "role": "Verifier"},
        ],
    },
    "hybrid": {
        "cost_efficient": [
            {"provider": "openai", "model": "gpt-4.1-mini", "role": "OpenAI"},
            {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "role": "Claude"},
        ],
        "all_time_best": [
            {"provider": "openai", "model": "gpt-4.1", "role": "OpenAI"},
            {"provider": "anthropic", "model": "claude-opus-4-7", "role": "Claude"},
        ],
        "turbo": [
            {"provider": "openai", "model": "gpt-4o", "role": "OpenAI"},
            {"provider": "anthropic", "model": "claude-sonnet-4-6", "role": "Claude"},
        ],
    },
    "google": {
        "cost_efficient": [
            {"provider": "google", "model": "gemini-3.1-flash-lite-preview", "role": "Scout"},
            {"provider": "google", "model": "gemini-3-flash-preview", "role": "Reviewer"},
        ],
        "all_time_best": [
            {"provider": "google", "model": "gemini-3.1-pro-preview", "role": "Lead"},
            {"provider": "google", "model": "gemini-3-flash-preview", "role": "Cross-check"},
        ],
        "turbo": [
            {"provider": "google", "model": "gemini-3-flash-preview", "role": "Turbo"},
            {"provider": "google", "model": "gemini-3.1-flash-lite-preview", "role": "Verifier"},
        ],
    },
}


def _format_elapsed(seconds: float) -> str:
    return f"{seconds:.2f}s"


def build_review_run(
    doc_paths: list[Path],
    *,
    run_dir: str | Path | None = None,
    use_ai_review: bool = False,
    model: str = "gpt-4.1-mini",
    progress_callback: Callable[[float, str], None] | None = None,
    copy_rules: list[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    overall_started = time.perf_counter()
    runtime = ensure_runtime_dirs()
    target_dir = Path(run_dir) if run_dir is not None else runtime.ui_runs_dir / _new_run_id()
    target_dir.mkdir(parents=True, exist_ok=True)

    ai_client = None
    if use_ai_review:
        try:
            ai_client = OpenAIResponsesClient(model=model)
        except ValueError:
            ai_client = None
        if openai_runtime_disabled():
            ai_client = None

    if progress_callback:
        progress_callback(5.0, "Extracting facts")
    store, total_facts = extract_fact_store(
        doc_paths, target_dir,
        ai_client=_make_omni_client(),
        copy_rules=copy_rules or [],
    )

    prewarmed_assets = False
    # Pre-warm is skipped here — AI review now only covers needs_review questions,
    # not all evidence-backed answers.  The corpus/RAG/Brain-F assets are not
    # needed for the strict-review-only pass.
    # (was: if use_ai_review: _prewarm_all_assets(...))

    registry = load_question_registry()
    print("\n=== Brain D — Answering questions ===")
    if progress_callback:
        progress_callback(20.0, "Answering questions")
    brain_d_started = time.perf_counter()
    answers = answer_all_questions(registry.values(), store, ai_client=ai_client)
    _apply_answer_fallbacks(answers, store)
    print(f"  [Time] Brain D total: {_format_elapsed(time.perf_counter() - brain_d_started)}")

    ai_review_results: dict[str, dict[str, Any]] = {}
    if use_ai_review and ai_client is not None and not openai_runtime_disabled():
        print("\n=== AI Review — Verifying answers ===")
        if progress_callback:
            progress_callback(50.0, "AI Review")
        ai_review_results = run_ai_review(answers, registry, ai_client)
        answers = _apply_ai_review_overrides(answers, registry, ai_review_results)
        (target_dir / "ai_review.json").write_text(
            json.dumps(ai_review_results, indent=2, default=str),
            encoding="utf-8",
        )
    _write_answers(target_dir / "answers.json", answers)

    print("\n=== Brain E — Building action plan ===")
    if progress_callback:
        progress_callback(75.0, "Building Matter")
    brain_e_started = time.perf_counter()
    action_plan = build_action_plan(answers, runtime.yaml_dir)
    (target_dir / "action_plan.json").write_text(
        action_plan.model_dump_json(indent=2),
        encoding="utf-8",
    )
    print(f"  [Time] Brain E total: {_format_elapsed(time.perf_counter() - brain_e_started)}")
    print("\n=== Writing summary ===")
    if progress_callback:
        progress_callback(90.0, "Writing summary")
    summary_started = time.perf_counter()
    write_summary(answers, target_dir)
    print(f"  [Time] Summary write total: {_format_elapsed(time.perf_counter() - summary_started)}")

    manifest = {
        "run_id": target_dir.name,
        "created_at": datetime.now(UTC).isoformat(),
        "document_count": len(doc_paths),
        "use_ai_review": bool(ai_review_results),
        "model": model,
        "client_name": _extract_client_name_from_store(store),
        "total_facts": total_facts,
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    print(f"\n[Time] Review run total: {_format_elapsed(time.perf_counter() - overall_started)}")

    # === Optional pre-warm so the UI is fully ready on first load ===
    # This intentionally re-reads documents to build the corpus/RAG/Brain-F assets.
    # DEFAULT IS NOW OFF — enable with `TRICONVEY_PREWARM_ASSETS=1`.
    # Pre-warming sends all documents through AI extraction which adds 15-45s per
    # document bundle and is the primary source of "too much AI" slowness.
    if (not prewarmed_assets) and str(os.getenv("TRICONVEY_PREWARM_ASSETS", "0")).strip().lower() not in {"0", "false", "no", "off"}:
        if progress_callback:
            progress_callback(95.0, "Warming assets")
        # Run corpus extraction + RAG + Brain F assets synchronously so the user
        # never hits a "still loading" state after the review screen appears.
        _prewarm_all_assets(doc_paths, target_dir, model=model)

    if progress_callback:
        progress_callback(100.0, "Complete")

    return load_run_payload(target_dir)


def save_review_answers(
    run_dir: str | Path,
    updates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    runtime = ensure_runtime_dirs()
    target_dir = Path(run_dir)
    answers_path = target_dir / "answers.json"
    current = json.loads(answers_path.read_text(encoding="utf-8"))
    answers: dict[str, AnswerObject] = {}

    for qid, raw_answer in current.items():
        answer = AnswerObject(**raw_answer)
        patch = updates.get(qid)
        if patch is not None:
            next_hints = dict(answer.presentation_hints)
            next_hints["ui_override"] = {
                "edited_at": datetime.now(UTC).isoformat(),
                "manual_review_cleared": not bool(patch.get("needs_review", False)),
            }
            answer = answer.model_copy(
                update={
                    "value": patch.get("value"),
                    "needs_review": bool(patch.get("needs_review", False)),
                    "review_reasons": list(answer.review_reasons) if patch.get("needs_review", False) else [],
                    "confidence": answer.confidence if patch.get("needs_review", False) else max(answer.confidence, 0.99),
                    "presentation_hints": next_hints,
                }
            )
        answers[qid] = answer

    _write_answers(answers_path, answers)
    action_plan = build_action_plan(answers, runtime.yaml_dir)
    (target_dir / "action_plan.json").write_text(
        action_plan.model_dump_json(indent=2),
        encoding="utf-8",
    )
    write_summary(answers, target_dir)
    return load_run_payload(target_dir)


def _apply_ai_review_overrides(
    answers: dict[str, AnswerObject],
    registry: dict[str, Any],
    ai_review_results: dict[str, dict[str, Any]],
) -> dict[str, AnswerObject]:
    updated = dict(answers)
    minimum_confidence = max(0.0, min(float(os.getenv("TRICONVEY_AI_REVIEW_MIN_CONFIDENCE", "0.85")), 1.0))

    for qid, answer in answers.items():
        review = ai_review_results.get(qid)
        if not isinstance(review, dict):
            continue
        if review.get("status") != "suggest_change":
            continue
        if not bool(review.get("quote_verified")):
            continue
        suggested_value = review.get("suggested_value")
        if suggested_value in (None, "", []):
            continue
        if suggested_value == answer.value:
            continue
        confidence = _coerce_ai_review_confidence(review.get("confidence"))
        if confidence < minimum_confidence:
            continue
        question = registry.get(qid)
        if question is None:
            continue

        next_hints = dict(answer.presentation_hints)
        next_hints["field_id"] = qid
        next_hints["answer_origin"] = "ai_review"
        next_hints["authoritative_value"] = answer.value
        next_hints["authoritative_confidence"] = answer.confidence
        next_hints["ai_review_reason"] = str(review.get("reason") or "")
        next_hints["ai_review_source_file"] = str(review.get("source_file") or "")

        updated[qid] = answer.model_copy(
            update={
                "value": _coerce_review_value(question, suggested_value),
                "confidence": max(answer.confidence, confidence),
                "needs_review": False,
                "review_reasons": [],
                "presentation_hints": next_hints,
            }
        )
    return updated


def _coerce_ai_review_confidence(raw_value: Any) -> float:
    if isinstance(raw_value, (int, float)):
        return max(0.0, min(float(raw_value), 1.0))
    if isinstance(raw_value, str):
        cleaned = raw_value.strip().lower()
        qualitative = {
            "very high": 0.95,
            "high": 0.9,
            "medium": 0.7,
            "moderate": 0.7,
            "low": 0.4,
            "very low": 0.2,
        }
        if cleaned in qualitative:
            return qualitative[cleaned]
        try:
            return max(0.0, min(float(cleaned), 1.0))
        except ValueError:
            return 0.0
    return 0.0


def _coerce_review_value(question: Any, suggested_value: Any) -> Any:
    expected_type = getattr(question, "expected_type", None)
    if expected_type == "bool":
        if isinstance(suggested_value, bool):
            return suggested_value
        if isinstance(suggested_value, str):
            return suggested_value.strip().lower() in {"1", "true", "yes", "y", "checked"}
    if expected_type == "number":
        try:
            return float(suggested_value)
        except (TypeError, ValueError):
            return suggested_value
    return suggested_value


def autofill_run(
    run_dir: str | Path,
    *,
    dry_run: bool = False,
    triconvey_exe: str | None = None,
    skip_review_gate: bool = False,
    cancel_requested=None,
    resume_from_property_details: bool = False,
    preferred_autofill_fields: list[str] | None = None,
) -> dict[str, Any]:
    target_dir = Path(run_dir)
    plan_path = target_dir / "action_plan.json"
    plan = FormActionPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    plan = _apply_preferred_autofill_filter(
        plan,
        preferred_autofill_fields if preferred_autofill_fields is not None else load_local_settings().get("preferredAutofillFields", []),
    )
    if plan.review_gate_required and not skip_review_gate:
        raise ValueError("This run still has review-gated fields.")

    from triconvey_agent.canonical.brain_e import execute_action_plan  # lazy to avoid circular import
    report = execute_action_plan(
        plan,
        client_name=_extract_client_name(target_dir),
        matter_search=_extract_matter_search(target_dir),
        dry_run=dry_run,
        triconvey_exe=triconvey_exe,
        review_gate_callback=lambda _items: True if skip_review_gate or dry_run else False,
        output_dir=target_dir,
        cancel_requested=cancel_requested,
        resume_from_property_details=resume_from_property_details,
    )
    (target_dir / "execution_report.json").write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return load_run_payload(target_dir)


def _apply_preferred_autofill_filter(plan: FormActionPlan, preferred_fields: list[str] | None) -> FormActionPlan:
    selected = {str(item).strip() for item in (preferred_fields or []) if str(item).strip()}
    if not selected:
        return plan

    # These Section 32 final items are always present in TriConvey and should
    # not disappear just because a user's preferred-field list is narrower.
    mandatory_question_ids = {
        "policy_6_due_diligence",
        "policy_6_attachments",
    }
    registry = load_question_registry()
    filtered_actions = []
    selected_review_gate_required = False
    for action in plan.actions:
        question = registry.get(action.question_id)
        label = getattr(question, "label", "") if question else ""
        if action.question_id in selected or label in selected or action.question_id in mandatory_question_ids:
            filtered_actions.append(action)
            if action.needs_review_first and action.action != "skip":
                selected_review_gate_required = True
            continue
        filtered_actions.append(
            action.model_copy(
                update={
                    "action": "skip",
                    "payload": None,
                    "expected_after": None,
                    "needs_review_first": False,
                }
            )
        )
    return plan.model_copy(update={"actions": filtered_actions, "review_gate_required": selected_review_gate_required})


def ensure_local_convey_running(*, triconvey_exe: str | None = None) -> bool:
    try:
        from triconvey_agent.canonical.brain_e.executor import TriConveyAgent  # lazy import for local desktop only

        agent = TriConveyAgent(triconvey_exe=triconvey_exe)
        return bool(agent.launch_or_connect())
    except Exception:
        return False


def check_triconvey_running_passive() -> bool:
    """Check whether triConvey.exe is running without touching its window."""
    try:
        import psutil
        return any(
            "triconvey" in (p.name() or "").lower()
            for p in psutil.process_iter(["name"])
        )
    except Exception:
        return False


def extract_corpus_entry_async(
    run_dir: str | Path,
    document_path: str | Path,
    document_id: str,
    matter_id: str,
    run_id: str,
    model: str = "gpt-4.1-mini",
) -> None:
    """Extract corpus entry for a single document in a background thread.

    The entry is written to corpus.json as *pending* so the user can confirm
    it in the chat before it becomes part of the permanent corpus.
    """
    def _run() -> None:
        try:
            from triconvey_agent.corpus.builder import add_pending, load_corpus, save_corpus
            from triconvey_agent.corpus.extractor import extract_corpus_entry
            from triconvey_agent.ingest.pdf_loader import load_pdf_document

            doc_path = Path(document_path)
            pdf_doc = load_pdf_document(doc_path)
            full_text = pdf_doc.full_text if hasattr(pdf_doc, "full_text") else _pdf_full_text(doc_path)
            page_count = pdf_doc.page_count if hasattr(pdf_doc, "page_count") else 0
            doc_type = _guess_document_type(doc_path.name)

            resolved_provider = _provider_from_model(model)
            ai_client = _make_ai_client_for_corpus(resolved_provider, model)
            entry = extract_corpus_entry(
                document_id=document_id,
                filename=doc_path.name,
                document_type=doc_type,
                full_text=full_text,
                page_count=page_count,
                ai_client=ai_client,
                model=model,
                provider=resolved_provider,
            )

            corpus = load_corpus(Path(run_dir), matter_id=matter_id, run_id=run_id)
            add_pending(corpus, entry, Path(run_dir))
            print(f"  [Corpus] Pending entry created for {doc_path.name}")
        except Exception as exc:
            print(f"  [WARN] Corpus extraction failed for {document_path}: {exc}")

    threading.Thread(target=_run, daemon=True).start()


def confirm_corpus_document(run_dir: str | Path, document_id: str, matter_id: str, run_id: str) -> dict[str, Any] | None:
    """Confirm a pending corpus entry — called when user confirms in chat."""
    from triconvey_agent.corpus.builder import confirm_document, load_corpus
    corpus = load_corpus(Path(run_dir), matter_id=matter_id, run_id=run_id)
    confirmed = confirm_document(corpus, document_id, Path(run_dir))
    if confirmed is None:
        return None
    return confirmed.to_dict()


def get_corpus_pending(run_dir: str | Path, matter_id: str, run_id: str) -> list[dict[str, Any]]:
    """Return pending corpus entries awaiting user confirmation."""
    from triconvey_agent.corpus.builder import load_corpus, pending_summary
    corpus = load_corpus(Path(run_dir), matter_id=matter_id, run_id=run_id)
    return pending_summary(corpus)


def get_corpus_state(run_dir: str | Path, matter_id: str, run_id: str) -> dict[str, Any]:
    """Return full corpus state for the run — confirmed + pending."""
    from triconvey_agent.corpus.builder import build_corpus_context, load_corpus, pending_summary
    from triconvey_agent.corpus.schema import MatterCorpus
    corpus = load_corpus(Path(run_dir), matter_id=matter_id, run_id=run_id)
    return {
        "matter_id": corpus.matter_id,
        "run_id": corpus.run_id,
        "document_count": len(corpus.documents),
        "pending_count": len(corpus.pending),
        "documents": [d.to_dict() for d in corpus.documents],
        "pending": pending_summary(corpus),
        "updated_at": corpus.updated_at,
        "context_preview": build_corpus_context(corpus)[:1000] if corpus.documents else "",
    }


def _pdf_full_text(path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = pdf.pages[:20]  # cap at 20 pages
            return "\n\n".join(p.extract_text() or "" for p in pages)
    except Exception:
        return ""


def _guess_document_type(filename: str) -> str:
    name = filename.lower()
    if "title" in name:
        return "vic_title"
    if "council" in name or "rates" in name:
        return "council_rates"
    if "water" in name:
        return "water_authority"
    if "planning" in name:
        return "planning_certificate"
    if "building" in name and "permit" in name:
        return "building_approval"
    if "owner" in name or "corporation" in name or " oc " in name:
        return "owners_corporation"
    if "land tax" in name or "sro" in name or "revenue" in name:
        return "land_tax"
    if "vendor" in name or "section 32" in name or "s32" in name:
        return "vendor_form"
    return "general"


def process_chat_document(
    run_dir: str | Path,
    doc_path: str | Path,
    model: str = "gpt-4.1-mini",
    matter_id: str = "",
    run_id: str = "",
    copy_rules: list[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    """Process a single newly chat-uploaded document.

    Returns validation result, extracted corpus data, and proposed answer changes.
    The caller (API endpoint) presents this to the user for approval.
    """
    from triconvey_agent.corpus.incremental import process_single_document
    run_id = run_id or Path(run_dir).name
    matter_id = matter_id or run_id
    return process_single_document(
        doc_path=Path(doc_path),
        run_dir=Path(run_dir),
        model=model,
        provider=_provider_from_model(model),
        run_id=run_id,
        matter_id=matter_id,
        copy_rules=copy_rules or [],
    )


def apply_chat_document_changes(
    run_dir: str | Path,
    document_id: str,
    doc_path: str | Path,
    corpus_entry_dict: dict[str, Any] | None,
    approved_change_ids: list[str],
    all_changes: list[dict[str, Any]],
    matter_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Apply user-approved changes from a single document processing result."""
    from triconvey_agent.corpus.incremental import apply_document_changes
    run_id = run_id or Path(run_dir).name
    matter_id = matter_id or run_id
    return apply_document_changes(
        run_dir=Path(run_dir),
        document_id=document_id,
        doc_path=Path(doc_path),
        corpus_entry_dict=corpus_entry_dict,
        approved_change_ids=approved_change_ids,
        all_changes=all_changes,
        matter_id=matter_id,
        run_id=run_id,
    )


def _prewarm_all_assets(doc_paths: list[Path], run_dir: Path, model: str = "gpt-4.1-mini") -> None:
    """Run corpus extraction, RAG index build, and Brain F asset warming synchronously.

    Called at the end of build_review_run so everything is ready when the
    review screen loads. This adds ~15-45 seconds per document bundle but
    eliminates all "loading…" delays in the UI.
    """
    from uuid import uuid4 as _uuid4

    run_id = run_dir.name

    print("\n=== Pre-warming AI assets (corpus + RAG + Brain F) ===")
    prewarm_started = time.perf_counter()

    # 1. Corpus extraction — extract structured data from all documents
    try:
        from triconvey_agent.corpus.builder import add_pending, load_corpus, save_corpus
        from triconvey_agent.corpus.extractor import extract_corpus_entry
        from triconvey_agent.ingest.pdf_loader import load_pdf_document

        corpus = load_corpus(run_dir, matter_id=run_id, run_id=run_id)
        provider = _provider_from_model(model)
        ai_client = None if openai_runtime_disabled() else _make_ai_client_for_corpus(provider, model)

        for doc_path in doc_paths:
            if openai_runtime_disabled():
                print("  [Corpus] AI unavailable — skipping remaining corpus AI extraction")
                break
            try:
                pdf_doc = load_pdf_document(doc_path)
                full_text = pdf_doc.full_text if hasattr(pdf_doc, "full_text") else _pdf_full_text(doc_path)
                page_count = pdf_doc.page_count if hasattr(pdf_doc, "page_count") else 0
                doc_type = _guess_document_type(doc_path.name)

                entry = extract_corpus_entry(
                    document_id=str(_uuid4()),
                    filename=doc_path.name,
                    document_type=doc_type,
                    full_text=full_text,
                    page_count=page_count,
                    ai_client=ai_client,
                    model=model,
                    provider=provider,
                )
                add_pending(corpus, entry, run_dir)
                corpus.confirm_pending(entry.document_id)
                print(f"  [Corpus] {doc_path.name} ✓")
            except Exception as exc:
                print(f"  [WARN] Corpus extraction failed for {doc_path.name}: {exc}")

        save_corpus(corpus, run_dir)
        print(f"  [Corpus] {len(corpus.documents)} document(s) in corpus")
    except Exception as exc:
        print(f"  [WARN] Corpus build failed: {exc}")

    # 2. RAG index — semantic search over document chunks
    try:
        from triconvey_agent.corpus.rag import build_rag_index
        build_rag_index(doc_paths, run_dir, force=True)
        print("  [RAG] Index built ✓")
    except Exception as exc:
        print(f"  [WARN] RAG index build failed: {exc}")

    # 3. Brain F assets — document corpus chunks + memory summaries
    try:
        _build_brain_f_assets(run_dir, deferred=False, include_memory=True)
        print("  [Brain F] Assets ready ✓")
    except Exception as exc:
        print(f"  [WARN] Brain F asset build failed: {exc}")

    print(f"  [Time] Pre-warm total: {_format_elapsed(time.perf_counter() - prewarm_started)}")


def _trigger_initial_corpus_extraction(doc_paths: list[Path], run_dir: Path, model: str = "gpt-4.1-mini") -> None:
    """Extract and auto-confirm corpus entries for initial run documents in a background thread.

    Initial documents don't require user confirmation — they are auto-confirmed
    because they were explicitly uploaded when creating the run.
    Chat-uploaded additions still go through the pending/confirm flow.
    """
    from uuid import uuid4 as _uuid4

    def _run() -> None:
        try:
            from triconvey_agent.corpus.builder import confirm_all_pending, load_corpus, save_corpus, add_pending
            from triconvey_agent.corpus.extractor import extract_corpus_entry
            from triconvey_agent.ingest.pdf_loader import load_pdf_document

            run_id = run_dir.name
            corpus = load_corpus(run_dir, matter_id=run_id, run_id=run_id)

            provider = _provider_from_model(model)
            ai_client = None if openai_runtime_disabled() else _make_ai_client_for_corpus(provider, model)

            for doc_path in doc_paths:
                if openai_runtime_disabled():
                    print("  [Corpus] AI unavailable — skipping remaining initial corpus extraction")
                    break
                try:
                    pdf_doc = load_pdf_document(doc_path)
                    full_text = pdf_doc.full_text if hasattr(pdf_doc, "full_text") else _pdf_full_text(doc_path)
                    page_count = pdf_doc.page_count if hasattr(pdf_doc, "page_count") else 0
                    doc_type = _guess_document_type(doc_path.name)

                    entry = extract_corpus_entry(
                        document_id=str(_uuid4()),
                        filename=doc_path.name,
                        document_type=doc_type,
                        full_text=full_text,
                        page_count=page_count,
                        ai_client=ai_client,
                        model=model,
                        provider=provider,
                    )
                    # Add to pending then immediately confirm (auto-confirm for initial docs)
                    add_pending(corpus, entry, run_dir)
                    corpus.confirm_pending(entry.document_id)
                    print(f"  [Corpus] Auto-confirmed entry for {doc_path.name}")
                except Exception as exc:
                    print(f"  [WARN] Corpus extraction failed for {doc_path.name}: {exc}")

            save_corpus(corpus, run_dir)
            print(f"  [Corpus] Initial corpus complete: {len(corpus.documents)} document(s)")
        except Exception as exc:
            print(f"  [WARN] Initial corpus extraction thread failed: {exc}")

    threading.Thread(target=_run, daemon=True).start()


def _provider_from_model(model: str) -> str:
    """Auto-detect provider from model name — same logic as ask_run_question."""
    if any(model.startswith(m) for m in ("claude",)):
        return "anthropic"
    if model.startswith("gemini"):
        return "google"
    return "openai"


def _make_omni_client() -> OpenRouterClient | None:
    """Return an OpenRouterClient when OPENROUTER_API_KEY is set, else None."""
    try:
        return OpenRouterClient()
    except ValueError:
        return None


def _make_ai_client_for_corpus(provider: str, model: str) -> Any:
    if provider == "anthropic":
        import anthropic
        return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    elif provider == "google":
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY", ""))
        return genai.GenerativeModel(model)
    else:
        import openai
        return openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


def warm_brain_f_assets_async(run_dir: str | Path) -> bool:
    """Warm Brain F assets in the background after the review UI loads.

    Returns True when a new warmup thread was started, False when the assets
    were already ready or a warmup was already in progress.
    """
    target_dir = Path(run_dir)
    if _brain_f_assets_ready(target_dir):
        return False

    key = str(target_dir.resolve())
    with _BRAIN_F_BUILD_LOCK:
        existing = _BRAIN_F_BUILD_EVENTS.get(key)
        if existing is not None and not existing.is_set():
            return False
        done = threading.Event()
        _BRAIN_F_BUILD_EVENTS[key] = done

    worker = threading.Thread(
        target=_background_brain_f_build,
        args=(target_dir, done, _BRAIN_F_DEFAULT_WARMUP_DELAY_SECONDS),
        daemon=True,
        name=f"brainf-warmup-{target_dir.name}",
    )
    worker.start()
    return True


def _infer_provider(model: str, preferred: str) -> str:
    """Auto-detect the correct provider from the model name.

    Prevents the common bug of sending a Claude model to the OpenAI API.
    """
    m = model.lower()
    if m.startswith("claude-"):
        return "anthropic"
    if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if m.startswith("gemini"):
        return "google"
    return preferred


def _normalize_brain_f_mode(mode: str) -> str:
    value = (mode or "standard").strip().lower()
    if value == "basic":
        return "quick"
    if value == "normal":
        return "standard"
    if value == "deep":
        return "deep"
    if value == "thorough":
        return "thorough"
    return value or "standard"


def ask_run_question(
    run_dir: str | Path,
    *,
    question: str,
    model: str = "gpt-4.1-mini",
    history: list[dict[str, Any]] | None = None,
    ai_provider: str = "openai",
    ai_mode: str = "cost_efficient",
    mode: str = "standard",
    session_id: str | None = None,
    vector_memories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Answer a question about a run using Brain F (agentic, tool-use).

    Auto-detects provider from model name so Claude models always use the
    Anthropic client even if the settings say 'openai'.

    Args:
        ai_provider:     Preferred provider — overridden if model name implies different.
        model:           Model name (e.g. "gpt-4o", "claude-sonnet-4-6").
        mode:            "quick" | "standard" | "thorough" — controls tool rounds + critic.
        session_id:      Feature 4 — chat session UUID for vector memory scoping.
        vector_memories: Feature 4 — pre-fetched vector memory entries to inject.
    """
    import os
    target_dir = Path(run_dir)
    normalized_mode = _normalize_brain_f_mode(mode)
    _ensure_brain_f_assets(target_dir, include_memory=normalized_mode in {"deep", "thorough"})
    execution_plan = _build_ai_execution_plan(
        preferred_provider=ai_provider,
        ai_mode=ai_mode,
        requested_model=model,
    )

    if not execution_plan["agents"]:
        return _ask_openai_fallback(target_dir, question=question, model=model)

    if len(execution_plan["agents"]) == 1:
        agent_cfg = execution_plan["agents"][0]
        result = _ask_brain_f(
            target_dir,
            question=question,
            history=history or [],
            model=agent_cfg["model"],
            provider=agent_cfg["provider"],
            mode=normalized_mode,
            persist_chat=True,
            session_id=session_id,
            vector_memories=vector_memories,
        )
        result["agent_runs"] = [
            {
                "provider": agent_cfg["provider"],
                "model": agent_cfg["model"],
                "role": agent_cfg["role"],
                "status": "completed",
            }
        ]
        result["summary_model"] = agent_cfg["model"]
        result["summary_provider"] = agent_cfg["provider"]
        return result

    return _ask_brain_f_collaborative(
        target_dir,
        question=question,
        history=history or [],
        mode=normalized_mode,
        execution_plan=execution_plan,
    )


def _ask_brain_f(
    target_dir: Path,
    *,
    question: str,
    history: list[dict[str, Any]],
    model: str,
    provider: str,
    mode: str = "standard",
    persist_chat: bool = True,
    session_id: str | None = None,
    vector_memories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from triconvey_agent.brain_f.agent import BrainFAgent

    store = _load_fact_store(target_dir)
    agent = BrainFAgent(
        store=store,
        run_dir=target_dir,
        model=model,
        provider=provider,
        persist_chat=persist_chat,
        session_id=session_id,
    )
    return agent.ask(
        question=question,
        history=history,
        mode=mode,
        vector_memories=vector_memories,
    )


def _ask_brain_f_collaborative(
    target_dir: Path,
    *,
    question: str,
    history: list[dict[str, Any]],
    mode: str,
    execution_plan: dict[str, Any],
) -> dict[str, Any]:
    agent_runs: list[dict[str, Any]] = []
    successful_results: list[dict[str, Any]] = []
    agent_configs = list(execution_plan.get("agents") or [])

    with ThreadPoolExecutor(max_workers=len(agent_configs)) as executor:
        future_map = {
            executor.submit(
                _ask_brain_f,
                target_dir,
                question=question,
                history=history,
                model=str(agent_cfg["model"]),
                provider=str(agent_cfg["provider"]),
                mode=mode,
                persist_chat=False,
            ): agent_cfg
            for agent_cfg in agent_configs
        }
        for future in as_completed(future_map):
            agent_cfg = future_map[future]
            run_info = {
                "provider": agent_cfg["provider"],
                "model": agent_cfg["model"],
                "role": agent_cfg["role"],
            }
            try:
                result = future.result()
                run_info["status"] = "completed"
                agent_runs.append(run_info)
                successful_results.append({"config": agent_cfg, "result": result})
            except Exception as exc:
                run_info["status"] = "failed"
                run_info["error"] = str(exc)
                agent_runs.append(run_info)

    if not successful_results:
        # Bubble up provider/model-specific errors so the UI can show the real cause
        # (e.g. missing env var, invalid model name, 401/429, network failure).
        raise ValueError(
            "No AI agent could complete the question. "
            f"Agent runs: {agent_runs}"
        )

    if len(successful_results) == 1:
        winner = successful_results[0]
        result = dict(winner["result"])
        result["agent_runs"] = agent_runs
        result["summary_model"] = winner["config"]["model"]
        result["summary_provider"] = winner["config"]["provider"]
        _persist_chat_summary(target_dir, question, str(result.get("answer") or ""))
        return result

    summary_model = str(execution_plan.get("summary_model") or successful_results[0]["config"]["model"])
    summary_provider = str(execution_plan.get("summary_provider") or successful_results[0]["config"]["provider"])
    combined_answer = _summarize_collaborative_answers(
        question=question,
        candidates=successful_results,
        summary_model=summary_model,
        summary_provider=summary_provider,
    )
    citations = _merge_citations([entry["result"] for entry in successful_results])
    proposed_patches = _merge_proposed_patches([entry["result"] for entry in successful_results])
    reasoning_steps = _merge_reasoning_steps(successful_results)
    best_result = max(
        successful_results,
        key=lambda entry: (
            len(entry["result"].get("citations") or []),
            entry["result"].get("tool_calls_made") or 0,
        ),
    )["result"]
    final_result = {
        "answer": combined_answer,
        "citations": citations,
        "proposed_patches": proposed_patches,
        "field_answers": [],
        "reasoning_steps": reasoning_steps,
        "tool_calls_made": sum(int(entry["result"].get("tool_calls_made") or 0) for entry in successful_results),
        "confidence_note": best_result.get("confidence_note"),
        "critic_applied": any(bool(entry["result"].get("critic_applied")) for entry in successful_results),
        "agent_runs": agent_runs,
        "summary_model": summary_model,
        "summary_provider": summary_provider,
    }
    _persist_chat_summary(target_dir, question, combined_answer)
    return final_result


def _build_ai_execution_plan(
    *,
    preferred_provider: str,
    ai_mode: str,
    requested_model: str,
) -> dict[str, Any]:
    provider = (preferred_provider or "openai").strip().lower()
    if provider not in {"openai", "anthropic", "hybrid", "google"}:
        provider = "openai"
    normalized_ai_mode = (ai_mode or "cost_efficient").strip().lower()
    if normalized_ai_mode not in _AI_MODE_CHOICES:
        normalized_ai_mode = "cost_efficient"

    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY"))
    has_google = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

    if provider == "hybrid" and not (has_openai and has_anthropic):
        provider = "openai" if has_openai else "anthropic"
    if provider == "google" and not has_google:
        if has_openai:
            provider = "openai"
        elif has_anthropic:
            provider = "anthropic"
        else:
            provider = "google"
    if provider == "openai" and not has_openai and has_anthropic:
        provider = "anthropic"
    if provider == "anthropic" and not has_anthropic and has_openai:
        provider = "openai"

    agents = [dict(item) for item in _AI_COLLABORATION_PRESETS.get(provider, _AI_COLLABORATION_PRESETS["openai"])[normalized_ai_mode]]
    if provider == "openai":
        agents = [item for item in agents if item["provider"] == "openai" and has_openai]
    elif provider == "anthropic":
        agents = [item for item in agents if item["provider"] == "anthropic" and has_anthropic]
    elif provider == "google":
        agents = [item for item in agents if item["provider"] == "google" and has_google]
    else:
        agents = [
            item for item in agents
            if (
                (item["provider"] == "openai" and has_openai)
                or (item["provider"] == "anthropic" and has_anthropic)
                or (item["provider"] == "google" and has_google)
            )
        ]

    inferred_provider = _infer_provider(requested_model, provider)
    if requested_model and inferred_provider in {"openai", "anthropic", "google"}:
        for agent in agents:
            if agent["provider"] == inferred_provider:
                agent["model"] = requested_model
                break

    if not agents and requested_model:
        provider_hint = _infer_provider(requested_model, provider)
        if provider_hint == "openai" and has_openai:
            agents = [{"provider": "openai", "model": requested_model, "role": "Solo"}]
        elif provider_hint == "anthropic" and has_anthropic:
            agents = [{"provider": "anthropic", "model": requested_model, "role": "Solo"}]
        elif provider_hint == "google" and has_google:
            agents = [{"provider": "google", "model": requested_model, "role": "Solo"}]

    summary_provider = "anthropic" if provider == "hybrid" and has_anthropic else (agents[0]["provider"] if agents else provider)
    summary_model = requested_model
    if not summary_model or _infer_provider(summary_model, summary_provider) != summary_provider:
        summary_model = _summary_model_for(summary_provider, normalized_ai_mode)
    return {
        "provider": provider,
        "ai_mode": normalized_ai_mode,
        "agents": agents,
        "summary_provider": summary_provider,
        "summary_model": summary_model,
    }


def _summary_model_for(provider: str, ai_mode: str) -> str:
    if provider == "anthropic":
        return {
            "cost_efficient": "claude-sonnet-4-6",
            "all_time_best": "claude-opus-4-7",
            "turbo": "claude-sonnet-4-6",
        }.get(ai_mode, "claude-sonnet-4-6")
    if provider == "google":
        return {
            "cost_efficient": "gemini-3.1-flash-lite-preview",
            "all_time_best": "gemini-3.1-pro-preview",
            "turbo": "gemini-3-flash-preview",
        }.get(ai_mode, "gemini-3-flash-preview")
    return {
        "cost_efficient": "gpt-4.1",
        "all_time_best": "gpt-4.1",
        "turbo": "gpt-4o",
    }.get(ai_mode, "gpt-4.1")


def _summarize_collaborative_answers(
    *,
    question: str,
    candidates: list[dict[str, Any]],
    summary_model: str,
    summary_provider: str,
) -> str:
    candidate_blocks = []
    for index, entry in enumerate(candidates, start=1):
        config = entry["config"]
        result = entry["result"]
        candidate_blocks.append(
            json.dumps(
                {
                    "agent": f"{config['provider']}:{config['model']}",
                    "role": config["role"],
                    "answer": result.get("answer"),
                    "citations": result.get("citations") or [],
                    "confidence_note": result.get("confidence_note"),
                },
                ensure_ascii=False,
            )
        )
    prompt = (
        "You are synthesizing multiple grounded legal-assistant answers about a conveyancing matter.\n"
        "Return one concise, accurate final answer. Prefer points supported by agreement between agents.\n"
        "If they disagree, note the uncertainty briefly and choose the more grounded statement.\n\n"
        f"Question:\n{question}\n\n"
        "Candidate answers:\n"
        + "\n".join(candidate_blocks)
    )
    if summary_provider == "google":
        try:
            from triconvey_agent.ai.multi_client import MultiModelClient

            client = MultiModelClient(provider="google", model=summary_model)
            resp = client.chat(
                [{"role": "user", "content": prompt}],
                system="You produce polished final answers from grounded agent outputs.",
                max_tokens=1024,
            )
            return (resp.content or "").strip()
        except Exception:
            pass
    if summary_provider == "anthropic":
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY"))
            resp = client.messages.create(
                model=summary_model,
                max_tokens=1024,
                system="You produce polished final answers from grounded agent outputs.",
                messages=[{"role": "user", "content": prompt}],
            )
            return "\n".join(block.text for block in resp.content if hasattr(block, "text")).strip()
        except Exception:
            pass
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model=summary_model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": "You produce polished final answers from grounded agent outputs."},
                {"role": "user", "content": prompt},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        pass
    best = max(candidates, key=lambda entry: len(entry["result"].get("citations") or []))
    return str(best["result"].get("answer") or "")


def _merge_citations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str | None, int | None, str]] = set()
    for result in results:
        for citation in result.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            key = (
                citation.get("file"),
                citation.get("page"),
                str(citation.get("quote") or "")[:120],
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(citation)
            if len(merged) >= 6:
                return merged
    return merged


def _merge_proposed_patches(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        for patch in result.get("proposed_patches") or []:
            if not isinstance(patch, dict):
                continue
            key = (str(patch.get("question_id") or ""), str(patch.get("new_value") or ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(patch)
    return merged


def _merge_reasoning_steps(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for entry in candidates:
        agent_label = f"{entry['config']['provider']}:{entry['config']['model']}"
        for step in entry["result"].get("reasoning_steps") or []:
            if not isinstance(step, dict):
                continue
            merged.append(
                {
                    "tool": f"{agent_label} -> {step.get('tool')}",
                    "input": step.get("input") or {},
                    "summary": step.get("summary") or "",
                }
            )
            if len(merged) >= 12:
                return merged
    return merged


def _persist_chat_summary(target_dir: Path, question: str, answer: str) -> None:
    try:
        from triconvey_agent.brain_f.agent import _load_chat_state, _save_chat_state

        state = _load_chat_state(target_dir)
        _save_chat_state(target_dir, state, question, answer)
    except Exception:
        return


def _ask_openai_fallback(
    target_dir: Path,
    *,
    question: str,
    model: str,
) -> dict[str, Any]:
    corpus_manifest = _read_json(target_dir / "document_corpus_manifest.json", default={})
    corpus_index_path = Path(str(corpus_manifest.get("index_path") or ""))
    if not corpus_index_path.exists():
        raise ValueError("Document corpus was not found for this run.")

    chunks = json.loads(corpus_index_path.read_text(encoding="utf-8"))
    ranked = _rank_corpus_chunks(question, chunks)
    if not ranked:
        return {"answer": "I could not find relevant document text for that question.", "citations": []}

    prompt = (
        "You are a Victorian conveyancing expert assistant. Answer the user's question "
        "using ONLY the supplied document excerpts below.\n"
        "Be precise, direct, and helpful. Explain legal implications when relevant.\n"
        "If the excerpts do not contain enough information, say so clearly.\n"
        "Return strict JSON with keys: answer (string) and citations (array).\n"
        "Each citation must include file, page, and quote.\n"
        "Use at least 3 citations from distinct excerpts when the corpus supports it.\n\n"
        f"Question:\n{question}\n\n"
        f"Document excerpts:\n{json.dumps(ranked[:10], ensure_ascii=False)}"
    )
    client = OpenAIResponsesClient(model=model)
    payload = _extract_json_object(client.complete(prompt).raw_text)
    if not isinstance(payload, dict):
        return {"answer": "I could not produce a structured answer from the document corpus.", "citations": []}
    citations = list(payload.get("citations") or [])
    if len(citations) < 3:
        seen: set[tuple[str | None, int | None]] = {(c.get("file"), c.get("page")) for c in citations if isinstance(c, dict)}
        for chunk in ranked:
            key = (chunk.get("file"), chunk.get("page"))
            if key in seen:
                continue
            citations.append({"file": chunk.get("file"), "page": chunk.get("page"), "quote": str(chunk.get("text") or "")[:320]})
            seen.add(key)
            if len(citations) >= 3:
                break
    return {
        "answer": str(payload.get("answer") or "").strip(),
        "citations": citations[:5],
    }


def _load_fact_store(run_dir: Path) -> FactStoreImpl:
    """Reload the FactStore from the saved facts.json for a run."""
    facts_path = run_dir / "facts.json"
    if not facts_path.exists():
        return FactStoreImpl()
    try:
        raw = json.loads(facts_path.read_text(encoding="utf-8"))
        schema = FactStore.model_validate(raw)
        return FactStoreImpl.from_schema(schema)
    except Exception:
        return FactStoreImpl()


def _apply_answer_fallbacks(answers: dict[str, AnswerObject], store: FactStoreImpl) -> None:
    bushfire_answer = answers.get("sec32_3.3_bushfire_prone")
    chosen_bushfire = _choose_bushfire_fact(store)
    if bushfire_answer is not None and chosen_bushfire is not None:
        if isinstance(chosen_bushfire.value, bool):
            answers["sec32_3.3_bushfire_prone"] = bushfire_answer.model_copy(
                update={
                    "value": chosen_bushfire.value,
                    "confidence": max(bushfire_answer.confidence, chosen_bushfire.confidence),
                    "facts_used": ["planning.bushfire_prone"],
                    "evidence": list(chosen_bushfire.sources),
                    "needs_review": False,
                    "review_reasons": [],
                }
            )
    _suppress_zero_outgoings(answers)
    _suppress_owners_corporation_outgoing(answers, store)
    _format_outgoing_authority_names(answers, store)


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return None
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(raw_text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _tokenize_question(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def _filename_hint_score(question: str, filename: str) -> float:
    q = (question or "").lower()
    name = (filename or "").lower()
    if not q or not name:
        return 0.0
    score = 0.0
    stem = name.rsplit(".", 1)[0]
    if name in q or stem in q:
        score += 8.0
    filename_tokens = _tokenize_question(stem.replace("_", " ").replace("-", " "))
    overlap = len(filename_tokens & _tokenize_question(q))
    score += overlap * 1.5
    if "state revenue" in q and ("state revenue" in name or "land tax" in name):
        score += 6.0
    if "land tax" in q and "land tax" in name:
        score += 6.0
    if "water" in q and "water" in name:
        score += 4.0
    if "council" in q and ("council" in name or "land information" in name):
        score += 4.0
    return score


def _rank_corpus_chunks(question: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q_tokens = _tokenize_question(question)
    query_embedding = _embed_query(question)
    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        filename = str(chunk.get("file") or "")
        overlap = len(q_tokens & _tokenize_question(text))
        score = float(overlap + _filename_hint_score(question, filename))
        if question.lower() in text.lower():
            score += 3.0
        embedding = chunk.get("embedding")
        if query_embedding and isinstance(embedding, list):
            score += _cosine_similarity(query_embedding, [float(x) for x in embedding]) * 8.0
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [chunk for _, chunk in scored]
    return chunks[:5]


def _write_document_corpus(doc_paths: list[Path], target_dir: Path) -> None:
    build_document_corpus(
        doc_paths,
        target_dir,
        progress_callback=lambda message: print(f"  [Brain F] {message}"),
        cache_only=True,
    )

def _embed_query(query: str) -> list[float] | None:
    # Disabled: embedding per search call adds 300-500 ms overhead.
    # Token+phrase matching in _rank_corpus_chunks is sufficient.
    return None


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)


def _choose_bushfire_fact(store: FactStoreImpl) -> Fact | None:
    """Pick the best bushfire fact based on confidence.

    The extractor already assigns appropriate confidence levels:
      - Explicit positive  ("is in a bushfire prone area")  → 0.98
      - Explicit negative  ("not designated bushfire prone") → 0.95
      - No mention at all  (ambiguous)                      → 0.50
      - Vendor form answer                                  → ~0.85

    So the highest-confidence fact naturally wins:
      - Planning cert explicit positive/negative beats vendor form
      - If planning cert is ambiguous (0.50), vendor form (0.85) wins
    """
    winner, _ = store.get("planning.bushfire_prone")
    if winner is not None and isinstance(winner.value, bool):
        return winner

    bool_facts = [fact for fact in store.get_all("planning.bushfire_prone") if isinstance(fact.value, bool)]
    if not bool_facts:
        return None
    bool_facts.sort(key=lambda f: f.confidence, reverse=True)
    return bool_facts[0]


def _parse_amount_like(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        cleaned = str(value).strip().replace("$", "").replace(",", "")
        if cleaned.lower() in {"n/a", "na", "-", "–", "—"}:
            return 0.0
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _clear_answer_value(answer: AnswerObject) -> AnswerObject:
    return answer.model_copy(
        update={
            "value": None,
            "needs_review": False,
            "review_reasons": [],
            "presentation_hints": {
                **dict(answer.presentation_hints),
                "suppressed": {
                    "reason": "outgoing_zero_amount",
                },
            },
        }
    )


def _suppress_zero_outgoings(answers: dict[str, AnswerObject]) -> None:
    for row in range(1, 5):
        authority_id = f"sec32_1.1_outgoing_{row}_authority"
        amount_id = f"sec32_1.1_outgoing_{row}_amount"
        amount_answer = answers.get(amount_id)
        authority_answer = answers.get(authority_id)
        if amount_answer is None:
            continue
        parsed = _parse_amount_like(amount_answer.value)
        if parsed is None or parsed > 0:
            continue
        answers[amount_id] = _clear_answer_value(amount_answer)
        if authority_answer is not None:
            answers[authority_id] = _clear_answer_value(authority_answer)


def _suppress_owners_corporation_outgoing(
    answers: dict[str, AnswerObject],
    store: FactStoreImpl,
) -> None:
    authority_id = "sec32_1.1_outgoing_4_authority"
    amount_id = "sec32_1.1_outgoing_4_amount"
    authority_answer = answers.get(authority_id)
    amount_answer = answers.get(amount_id)
    oc_exists, _ = store.get("rates.owners_corporation.exists")

    amount_value = _parse_amount_like(amount_answer.value) if amount_answer is not None else None
    has_positive_amount = amount_value is not None and amount_value > 0
    oc_is_active = bool(oc_exists and oc_exists.value is True)

    # Row 4 should only appear when an active OC exists and there is a real
    # positive annual amount to show. Generic fallback names with blank amounts
    # should stay invisible in the UI and autofill payload.
    if oc_is_active and has_positive_amount:
        return

    if amount_answer is not None:
        answers[amount_id] = _clear_answer_value(amount_answer)
    if authority_answer is not None:
        answers[authority_id] = _clear_answer_value(authority_answer)


# Authority name → suffix mapping for the 4 outgoing rows
_OUTGOING_AUTHORITY_SUFFIXES: dict[int, str] = {
    1: "Annually",   # council rates
    2: "Annually",   # water authority
    3: "Annually",   # land tax (SRO)
    4: "Annually",   # owners corporation
}

# Default names when the fact is absent but an amount exists
_OUTGOING_AUTHORITY_DEFAULTS: dict[int, str] = {
    1: "Council",
    2: "Water Authority",
    3: "State Revenue Office",
    4: "Owners Corporation Insurance",
}


def _format_outgoing_authority_names(
    answers: dict[str, AnswerObject],
    store: FactStoreImpl,
) -> None:
    """Append ' - Annually' to every populated outgoing authority name.

    For row 4 (OC), if no name is present but an amount exists, default
    the name to 'Owners Corporation Insurance - Annually'.
    """
    for row, suffix in _OUTGOING_AUTHORITY_SUFFIXES.items():
        authority_id = f"sec32_1.1_outgoing_{row}_authority"
        amount_id = f"sec32_1.1_outgoing_{row}_amount"
        authority_answer = answers.get(authority_id)
        amount_answer = answers.get(amount_id)

        has_amount = (
            amount_answer is not None
            and _parse_amount_like(amount_answer.value) is not None
            and (_parse_amount_like(amount_answer.value) or 0) > 0
        )

        current_name: str | None = None
        if authority_answer is not None:
            v = authority_answer.value
            current_name = str(v).strip() if v is not None else None
            if current_name == "":
                current_name = None

        # If no name but amount exists, use default
        if current_name is None and has_amount:
            current_name = _OUTGOING_AUTHORITY_DEFAULTS[row]

        if not current_name:
            continue

        # Don't double-append the suffix
        display = current_name
        if not display.endswith(f"- {suffix}") and not display.endswith(f"– {suffix}"):
            display = f"{display} - {suffix}"

        if authority_answer is not None:
            answers[authority_id] = authority_answer.model_copy(update={"value": display})
        else:
            # Create a placeholder answer carrying just the display name
            answers[authority_id] = AnswerObject(
                question_id=authority_id,
                question_label=f"1.1 Outgoing {row} - Authority name",
                value=display,
            )


def _remove_tree(root: Path) -> None:
    for child in sorted(root.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            child.rmdir()
    if root.exists():
        root.rmdir()


def load_run_payload(run_dir: str | Path) -> dict[str, Any]:
    target_dir = Path(run_dir)
    registry = load_question_registry()
    raw_answers = json.loads((target_dir / "answers.json").read_text(encoding="utf-8"))
    answers = {qid: AnswerObject(**payload) for qid, payload in raw_answers.items()}
    action_plan = _read_json(target_dir / "action_plan.json", default={})
    execution_report = _read_json(target_dir / "execution_report.json", default={})
    ai_review = _read_json(target_dir / "ai_review.json", default={})
    manifest = _read_json(target_dir / "manifest.json", default={"run_id": target_dir.name})
    facts_map = _load_facts_map(target_dir)
    store = _load_fact_store(target_dir)
    matter = _extract_matter_details(facts_map)
    tabs: dict[str, list[dict[str, Any]]] = {}

    for qid, answer in answers.items():
        question = registry.get(qid)
        tab_name = question.tab if question else "Unknown"
        tabs.setdefault(tab_name, []).append(
            {
                "question_id": qid,
                "label": answer.question_label,
                "expected_type": question.expected_type if question else None,
                "answer_strategy": answer.answer_strategy,
                "value": answer.value,
                "confidence": answer.confidence,
                "needs_review": answer.needs_review,
                "review_reasons": answer.review_reasons,
                "facts_used": answer.facts_used,
                "evidence": [source.model_dump(mode="json") for source in answer.evidence],
                "options": question.options if question else None,
                "description": question.description if question else None,
                "presentation_hints": {
                    "field_id": qid,
                    "answer_origin": str(dict(answer.presentation_hints).get("answer_origin") or "authoritative"),
                    **answer.presentation_hints,
                },
                "ai_review": ai_review.get(qid),
            }
        )

    for items in tabs.values():
        items.sort(key=lambda item: item["label"])

    ready_count = sum(1 for answer in answers.values() if not answer.needs_review and answer.value is not None)
    review_count = sum(1 for answer in answers.values() if answer.needs_review)
    auto_actions = len([action for action in action_plan.get("actions", []) if action.get("action") != "skip"])
    return {
        "manifest": manifest,
        "client_name": matter["client_name"],
        "matter": matter,
        "run_dir": str(target_dir),
        "corpus_path": str((_read_json(target_dir / "document_corpus_manifest.json", default={}) or {}).get("corpus_path") or ""),
        "chat_history": _read_json(target_dir / "chat_history.json", default={"summary": "", "turns": []}),
        "summary_text": (target_dir / "summary.txt").read_text(encoding="utf-8") if (target_dir / "summary.txt").exists() else "",
        "agent_context": {
            "fact_snapshot": _build_chat_fact_snapshot(store),
            "unresolved_conflicts": _format_unresolved_conflicts(store),
        },
        "tabs": [{"tab": tab_name, "items": items} for tab_name, items in sorted(tabs.items())],
        "metrics": {
            "total_questions": len(answers),
            "auto_ready": ready_count,
            "needs_review": review_count,
            "action_count": auto_actions,
            "review_gate_required": bool(action_plan.get("review_gate_required")),
            "filled": execution_report.get("total_filled", 0),
            "failed": execution_report.get("total_failed", 0),
            "pending_review": execution_report.get("total_pending_review", 0),
        },
        "action_plan": action_plan,
        "execution_report": execution_report,
    }


def _build_chat_fact_snapshot(store: FactStoreImpl) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for path in (
        "rates.council.authority_name",
        "rates.council.annual_amount",
        "rates.water.authority_name",
        "rates.water.annual_amount",
        "rates.land_tax.authority_name",
        "rates.land_tax.amount",
        "rates.owners_corporation.authority_name",
        "rates.owners_corporation.annual_amount",
        "planning.bushfire_prone",
        "planning.zone",
        "planning.overlay_names",
        "planning.responsible_authority",
    ):
        winner, _ = store.get(path)
        if winner is None or winner.value in (None, ""):
            continue
        source = winner.sources[0] if winner.sources else None
        snapshot.append(
            {
                "path": path,
                "value": winner.value,
                "confidence": winner.confidence,
                "extractor": winner.extractor,
                "file": source.file if source else None,
                "page": source.page if source else None,
            }
        )
    return snapshot


def _format_unresolved_conflicts(store: FactStoreImpl) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for conflict in store.unresolved_conflicts():
        values = []
        for fact in conflict.facts[:4]:
            source = fact.sources[0] if fact.sources else None
            values.append(
                {
                    "value": fact.value,
                    "extractor": fact.extractor,
                    "file": source.file if source else None,
                    "page": source.page if source else None,
                    "confidence": fact.confidence,
                }
            )
        rows.append(
            {
                "path": conflict.path,
                "reason": conflict.reason,
                "candidates": values,
            }
        )
    return rows


def _write_answers(path: Path, answers: dict[str, AnswerObject]) -> None:
    payload = {qid: answer.model_dump(mode="json") for qid, answer in answers.items()}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _extract_client_name_from_store(store: FactStoreImpl) -> str:
    try:
        title_fact, _ = store.get("vendor.0.title")
        first_fact, _ = store.get("vendor.0.first_name")
        last_fact, _ = store.get("vendor.0.last_name")
        title = str(title_fact.value).strip() if title_fact else ""
        first = str(first_fact.value).strip() if first_fact else ""
        last = str(last_fact.value).strip() if last_fact else ""
        if first and last:
            return " ".join(part for part in (title, first, last) if part)
        if first:
            return " ".join(part for part in (title, first) if part)
        preferred_fact, _ = store.get("vendor.0.preferred_name")
        if preferred_fact:
            return " ".join(part for part in (title, str(preferred_fact.value).strip()) if part)
    except Exception:
        return ""
    return ""


def _extract_client_name(run_dir: Path) -> str:
    try:
        return _extract_matter_details(_load_facts_map(run_dir))["client_name"]
    except Exception:
        return ""


def _extract_matter_search(run_dir: Path) -> str:
    try:
        matter = _extract_matter_details(_load_facts_map(run_dir))
        volume_folio = str(matter.get("volume_folio") or "").strip()
        if not volume_folio:
            return ""
        import re

        nums = re.findall(r"\d+", volume_folio)
        if len(nums) >= 2:
            return f"VOLUME {nums[0]} FOLIO {nums[1]}"
        return volume_folio.upper()
    except Exception:
        return ""


def _extract_matter_details(facts_map: dict[str, Any]) -> dict[str, str]:
    title = _get_first_fact_value(facts_map, "vendor.0.title")
    first = _get_first_fact_value(facts_map, "vendor.0.first_name")
    last = _get_first_fact_value(facts_map, "vendor.0.last_name")
    preferred = _get_first_fact_value(facts_map, "vendor.0.preferred_name")
    client_name = " ".join(part for part in (title, first, last) if part).strip()
    if not client_name:
        client_name = " ".join(part for part in (title, preferred) if part).strip()

    volume_folio = _get_first_fact_value(facts_map, "title.volume_folio")
    if not volume_folio:
        volume = _get_first_fact_value(facts_map, "title.volume")
        folio = _get_first_fact_value(facts_map, "title.folio")
        volume_folio = " ".join(
            part for part in (f"Volume {volume}" if volume else "", f"Folio {folio}" if folio else "") if part
        ).strip()

    property_address = (
        _get_first_fact_value(facts_map, "property.address")
        or _get_first_fact_value(facts_map, "title.street_address")
        or _get_first_fact_value(facts_map, "vendor.0.residential_address")
    )

    return {
        "client_name": client_name,
        "volume_folio": volume_folio,
        "property_address": property_address,
    }


def _load_facts_map(run_dir: Path) -> dict[str, Any]:
    facts_path = run_dir / "facts.json"
    if not facts_path.exists():
        return {}
    try:
        data = json.loads(facts_path.read_text(encoding="utf-8"))
        facts = data.get("facts", {})
        return facts if isinstance(facts, dict) else {}
    except Exception:
        return {}


def _get_first_fact_value(facts_map: dict[str, Any], path: str) -> str:
    entries = facts_map.get(path, [])
    if isinstance(entries, list):
        for fact in entries:
            if isinstance(fact, dict) and fact.get("value") not in (None, ""):
                return str(fact["value"]).strip()
    if isinstance(entries, dict):
        for fact in entries.get("facts", []):
            if isinstance(fact, dict) and fact.get("value") not in (None, ""):
                return str(fact["value"]).strip()
    return ""


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _ensure_brain_f_assets(target_dir: Path, *, include_memory: bool = False) -> None:
    if _brain_f_assets_ready(target_dir, include_memory=include_memory):
        return

    key = str(target_dir.resolve())
    with _BRAIN_F_BUILD_LOCK:
        existing = _BRAIN_F_BUILD_EVENTS.get(key)
        if existing is not None and not existing.is_set():
            event = existing
            should_build = False
        else:
            event = threading.Event()
            _BRAIN_F_BUILD_EVENTS[key] = event
            should_build = True

    if not should_build:
        event.wait()
        if _brain_f_assets_ready(target_dir, include_memory=include_memory):
            return

    try:
        _build_brain_f_assets(target_dir, deferred=True, include_memory=include_memory)
    finally:
        event.set()

def _new_run_id() -> str:
    """Returns a UUID4 string used as both the DB primary key and the run directory name."""
    return str(uuid4())


def _brain_f_assets_ready(target_dir: Path, *, include_memory: bool = False) -> bool:
    if not (target_dir / "document_corpus_manifest.json").exists():
        return False
    if include_memory and not (target_dir / "document_memory.json").exists():
        return False
    return True


def set_autofill_activity(run_dir: str | Path, active: bool) -> None:
    key = str(Path(run_dir).resolve())
    with _AUTOFILL_ACTIVITY_LOCK:
        if active:
            _ACTIVE_AUTOFILL_RUNS.add(key)
        else:
            _ACTIVE_AUTOFILL_RUNS.discard(key)


def _autofill_is_active(run_dir: str | Path) -> bool:
    key = str(Path(run_dir).resolve())
    with _AUTOFILL_ACTIVITY_LOCK:
        return key in _ACTIVE_AUTOFILL_RUNS


def _background_brain_f_build(
    target_dir: Path,
    done: threading.Event,
    warmup_delay_seconds: float,
) -> None:
    try:
        _wait_until_brain_f_warmup_safe(target_dir, warmup_delay_seconds)
        if _brain_f_assets_ready(target_dir):
            return
        _build_brain_f_assets(target_dir, deferred=False, include_memory=False)
    finally:
        print("  [Brain F] Background warmup finished")
        key = str(target_dir.resolve())
        with _BRAIN_F_BUILD_LOCK:
            current = _BRAIN_F_BUILD_EVENTS.get(key)
            if current is done:
                _BRAIN_F_BUILD_EVENTS.pop(key, None)
        done.set()


def _wait_until_brain_f_warmup_safe(target_dir: Path, warmup_delay_seconds: float) -> None:
    if warmup_delay_seconds > 0:
        deadline = time.monotonic() + warmup_delay_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))

    while _autofill_is_active(target_dir):
        time.sleep(1.0)


def _build_brain_f_assets(target_dir: Path, *, deferred: bool) -> None:
    corpus_manifest_path = target_dir / "document_corpus_manifest.json"
    memory_path = target_dir / "document_memory.json"
    uploads_dir = target_dir / "uploads"
    if not uploads_dir.exists():
        return
    doc_paths = sorted(
        path for path in uploads_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )
    if not doc_paths:
        return

    prefix = "Deferred" if deferred else "Background"
    try:
        if not corpus_manifest_path.exists():
            print(f"  [Brain F] Building {prefix.lower()} document corpus…")
            _write_document_corpus(doc_paths, target_dir)
    except Exception as exc:
        print(f"  [WARN] {prefix} document corpus build failed: {exc}")

    try:
        if not memory_path.exists():
            print(f"  [Brain F] Building {prefix.lower()} document memory…")
            store = _load_fact_store(target_dir)
            build_document_memory(doc_paths, store, target_dir)
    except Exception as exc:
        print(f"  [WARN] {prefix} document memory build failed: {exc}")


def _build_brain_f_assets(target_dir: Path, *, deferred: bool, include_memory: bool) -> None:
    corpus_manifest_path = target_dir / "document_corpus_manifest.json"
    memory_path = target_dir / "document_memory.json"
    uploads_dir = target_dir / "uploads"
    if not uploads_dir.exists():
        return
    doc_paths = sorted(
        path for path in uploads_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )
    if not doc_paths:
        return

    prefix = "Deferred" if deferred else "Background"
    try:
        if not corpus_manifest_path.exists():
            print(f"  [Brain F] Building {prefix.lower()} document corpus...")
            _write_document_corpus(doc_paths, target_dir)
            print(f"  [Brain F] {prefix} document corpus complete")
    except Exception as exc:
        print(f"  [WARN] {prefix} document corpus build failed: {exc}")

    if not include_memory:
        return

    try:
        if not memory_path.exists():
            print(f"  [Brain F] Building {prefix.lower()} document memory...")
            store = _load_fact_store(target_dir)
            build_document_memory(
                doc_paths,
                store,
                target_dir,
                progress_callback=lambda message: print(f"  [Brain F] {message}"),
            )
            print(f"  [Brain F] {prefix} document memory complete")
    except Exception as exc:
        print(f"  [WARN] {prefix} document memory build failed: {exc}")
