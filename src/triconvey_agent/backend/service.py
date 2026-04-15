from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from triconvey_agent.ai.openai_client import OpenAIResponsesClient
from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths
from triconvey_agent.canonical.brain_d import build_action_plan
from triconvey_agent.canonical.brain_e import execute_action_plan
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.questions.loader import load_question_registry
from triconvey_agent.canonical.router import answer_all_questions
from triconvey_agent.canonical.runner.ai_review import run_ai_review
from triconvey_agent.canonical.runner.fact_extraction import extract_fact_store
from triconvey_agent.canonical.runner.summary_writer import write_summary
from triconvey_agent.canonical.schemas import AnswerObject, FormActionPlan


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
    registry = load_question_registry()
    answers = answer_all_questions(registry.values(), store, ai_client=ai_client)
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
) -> dict[str, Any]:
    target_dir = Path(run_dir)
    plan_path = target_dir / "action_plan.json"
    plan = FormActionPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    if plan.review_gate_required and not skip_review_gate:
        raise ValueError("This run still has review-gated fields.")

    report = execute_action_plan(
        plan,
        client_name=_extract_client_name(target_dir),
        dry_run=dry_run,
        triconvey_exe=triconvey_exe,
        review_gate_callback=lambda _items: True if skip_review_gate or dry_run else False,
        output_dir=target_dir,
    )
    (target_dir / "execution_report.json").write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return load_run_payload(target_dir)


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
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
