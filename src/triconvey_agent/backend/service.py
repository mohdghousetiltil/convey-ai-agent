from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from triconvey_agent.ai.openai_client import OpenAIResponsesClient
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
_BRAIN_F_BUILD_LOCK = threading.Lock()
_BRAIN_F_BUILD_EVENTS: dict[str, threading.Event] = {}
_AUTOFILL_ACTIVITY_LOCK = threading.Lock()
_ACTIVE_AUTOFILL_RUNS: set[str] = set()
_BRAIN_F_DEFAULT_WARMUP_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("CONVEY_BRAIN_F_WARMUP_DELAY_SECONDS", "0")),
)


def build_review_run(
    doc_paths: list[Path],
    *,
    run_dir: str | Path | None = None,
    use_ai_review: bool = False,
    model: str = "gpt-4.1-mini",
) -> dict[str, Any]:
    runtime = ensure_runtime_dirs()
    target_dir = Path(run_dir) if run_dir is not None else runtime.ui_runs_dir / _new_run_id()
    target_dir.mkdir(parents=True, exist_ok=True)

    ai_client = None
    if use_ai_review:
        try:
            ai_client = OpenAIResponsesClient(model=model)
        except ValueError:
            ai_client = None

    store, total_facts = extract_fact_store(doc_paths, target_dir)

    # Keep run creation fast. Brain F assets are built lazily on first chat use
    # via _ensure_brain_f_assets(), so large PDF bundles do not block the review
    # screen immediately after Brain C.

    registry = load_question_registry()
    answers = answer_all_questions(registry.values(), store, ai_client=ai_client)
    _apply_answer_fallbacks(answers, store)
    _write_answers(target_dir / "answers.json", answers)

    ai_review_results: dict[str, dict[str, Any]] = {}
    if use_ai_review and ai_client is not None:
        ai_review_results = run_ai_review(answers, registry, ai_client)
        (target_dir / "ai_review.json").write_text(
            json.dumps(ai_review_results, indent=2, default=str),
            encoding="utf-8",
        )

    action_plan = build_action_plan(answers, runtime.yaml_dir)
    (target_dir / "action_plan.json").write_text(
        action_plan.model_dump_json(indent=2),
        encoding="utf-8",
    )
    write_summary(answers, target_dir)

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

    registry = load_question_registry()
    filtered_actions = []
    selected_review_gate_required = False
    for action in plan.actions:
        question = registry.get(action.question_id)
        label = getattr(question, "label", "") if question else ""
        if action.question_id in selected or label in selected:
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
    mode: str = "standard",
) -> dict[str, Any]:
    """Answer a question about a run using Brain F (agentic, tool-use).

    Auto-detects provider from model name so Claude models always use the
    Anthropic client even if the settings say 'openai'.

    Args:
        ai_provider: Preferred provider — overridden if model name implies different.
        model:       Model name (e.g. "gpt-4o", "claude-sonnet-4-6").
        mode:        "quick" | "standard" | "thorough" — controls tool rounds + critic.
    """
    import os
    target_dir = Path(run_dir)
    normalized_mode = _normalize_brain_f_mode(mode)
    _ensure_brain_f_assets(target_dir, include_memory=normalized_mode in {"deep", "thorough"})

    provider = _infer_provider(model, ai_provider)

    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        if key:
            return _ask_brain_f(target_dir, question=question, history=history or [],
                                model=model, provider="anthropic", mode=normalized_mode)
    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if key:
            return _ask_brain_f(target_dir, question=question, history=history or [],
                                model=model, provider="openai", mode=normalized_mode)

    return _ask_openai_fallback(target_dir, question=question, model=model)


def _ask_brain_f(
    target_dir: Path,
    *,
    question: str,
    history: list[dict[str, Any]],
    model: str,
    provider: str,
    mode: str = "standard",
) -> dict[str, Any]:
    from triconvey_agent.brain_f.agent import BrainFAgent

    store = _load_fact_store(target_dir)
    agent = BrainFAgent(store=store, run_dir=target_dir, model=model, provider=provider)
    return agent.ask(question=question, history=history, mode=mode)


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


def _rank_corpus_chunks(question: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q_tokens = _tokenize_question(question)
    query_embedding = _embed_query(question)
    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        overlap = len(q_tokens & _tokenize_question(text))
        score = float(overlap)
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
                "presentation_hints": answer.presentation_hints,
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
    except Exception as exc:
        print(f"  [WARN] {prefix} document memory build failed: {exc}")
