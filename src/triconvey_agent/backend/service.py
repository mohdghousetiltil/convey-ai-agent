from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from triconvey_agent.ai.openai_client import OpenAIResponsesClient
from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths
from triconvey_agent.canonical.brain_d import build_action_plan
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.questions.loader import load_question_registry
from triconvey_agent.canonical.router import answer_all_questions
from triconvey_agent.canonical.runner.ai_review import run_ai_review
from triconvey_agent.canonical.runner.fact_extraction import extract_fact_store
from triconvey_agent.canonical.runner.summary_writer import write_summary
from triconvey_agent.canonical.schemas import AnswerObject, FormActionPlan
from triconvey_agent.ingest.pdf_loader import load_pdf_document


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
    _write_document_corpus(doc_paths, target_dir)

    ai_client = None
    if use_ai_review:
        try:
            ai_client = OpenAIResponsesClient(model=model)
        except ValueError:
            ai_client = None

    store, total_facts = extract_fact_store(doc_paths, target_dir)
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
) -> dict[str, Any]:
    target_dir = Path(run_dir)
    plan_path = target_dir / "action_plan.json"
    plan = FormActionPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
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
    )
    (target_dir / "execution_report.json").write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return load_run_payload(target_dir)


def ensure_local_convey_running(*, triconvey_exe: str | None = None) -> bool:
    try:
        from triconvey_agent.canonical.brain_e.executor import TriConveyAgent  # lazy import for local desktop only

        agent = TriConveyAgent(triconvey_exe=triconvey_exe)
        return bool(agent.launch_or_connect())
    except Exception:
        return False


def ask_run_question(
    run_dir: str | Path,
    *,
    question: str,
    model: str = "gpt-4.1-mini",
) -> dict[str, Any]:
    target_dir = Path(run_dir)
    corpus_manifest = _read_json(target_dir / "document_corpus_manifest.json", default={})
    corpus_index_path = Path(str(corpus_manifest.get("index_path") or ""))
    if not corpus_index_path.exists():
        raise ValueError("Document corpus was not found for this run.")

    chunks = json.loads(corpus_index_path.read_text(encoding="utf-8"))
    ranked = _rank_corpus_chunks(question, chunks)
    if not ranked:
        return {"answer": "I could not find relevant document text for that question.", "citations": []}

    # Include extracted answers as context so the assistant knows what was already found
    answers_context = ""
    answers_path = target_dir / "answers.json"
    if answers_path.exists():
        try:
            raw_answers = json.loads(answers_path.read_text(encoding="utf-8"))
            summary_lines = []
            for qid, ans in raw_answers.items():
                val = ans.get("value")
                label = ans.get("question_label", qid)
                if val is not None:
                    summary_lines.append(f"- {label}: {val}")
            if summary_lines:
                answers_context = (
                    "\n\nAlready-extracted answers from the documents (use these for context):\n"
                    + "\n".join(summary_lines[:30])
                )
        except Exception:
            pass

    prompt = (
        "You are a Victorian conveyancing expert assistant. Answer the user's question "
        "using ONLY the supplied document excerpts and extracted answers.\n"
        "Be precise, direct, and helpful. Explain legal implications when relevant.\n"
        "If the excerpts do not contain enough information, say so clearly.\n"
        "Return strict JSON with keys: answer (string) and citations (array).\n"
        "Each citation must include file, page, and quote.\n"
        "Use at least 3 citations from distinct excerpts when the corpus supports it.\n"
        f"{answers_context}\n\n"
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
    scored: list[tuple[int, dict[str, Any]]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        overlap = len(q_tokens & _tokenize_question(text))
        if overlap:
            scored.append((overlap, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [chunk for _, chunk in scored]
    return chunks[:5]


def _write_document_corpus(doc_paths: list[Path], target_dir: Path) -> None:
    runtime = ensure_runtime_dirs()
    for existing in runtime.temp_corpus_dir.iterdir():
        if existing.is_dir():
            _remove_tree(existing)
    corpus_dir = runtime.temp_corpus_dir / target_dir.name
    if corpus_dir.exists():
        _remove_tree(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    lines: list[str] = []
    for doc_path in doc_paths:
        try:
            doc = load_pdf_document(doc_path)
        except Exception:
            continue
        page_texts = _clean_document_pages(doc)
        for page_number, page_text in page_texts:
            if not page_text.strip():
                continue
            index.append({"file": doc.filename, "page": page_number, "text": page_text})
            lines.append(f"FILE: {doc.filename}")
            lines.append(f"PAGE: {page_number}")
            lines.append(page_text)
            lines.append("")
    corpus_text_path = corpus_dir / "document_corpus.txt"
    corpus_index_path = corpus_dir / "document_corpus_index.json"
    corpus_text_path.write_text("\n".join(lines).strip(), encoding="utf-8")
    corpus_index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    (target_dir / "document_corpus_manifest.json").write_text(
        json.dumps(
            {
                "corpus_path": str(corpus_text_path),
                "index_path": str(corpus_index_path),
                "expires_after_hours": 24,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


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
        "summary_text": (target_dir / "summary.txt").read_text(encoding="utf-8") if (target_dir / "summary.txt").exists() else "",
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


def _new_run_id() -> str:
    """Returns a UUID4 string used as both the DB primary key and the run directory name."""
    return str(uuid4())
