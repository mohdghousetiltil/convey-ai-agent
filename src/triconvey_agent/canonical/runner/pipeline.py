from __future__ import annotations

import json
from pathlib import Path

from triconvey_agent.ai.client import AIClient
from triconvey_agent.canonical.brain_d import build_action_plan
from triconvey_agent.canonical.questions.loader import load_question_registry
from triconvey_agent.canonical.router import answer_all_questions

from .ai_review import run_ai_review
from .fact_extraction import extract_fact_store
from .summary_writer import write_summary


def run_pipeline(
    doc_paths: list[Path],
    out_dir: Path,
    yaml_dir: Path,
    *,
    ai_client: AIClient | None = None,
    use_ai_review: bool = False,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    store, total_facts = extract_fact_store(doc_paths, out_dir)

    print("\n=== Brain B — Answering registry questions ===")
    registry = load_question_registry()
    answers = answer_all_questions(registry.values(), store, ai_client=ai_client)

    ready = [a for a in answers.values() if not a.needs_review and a.value is not None]
    review = [a for a in answers.values() if a.needs_review]
    print(f"  Questions answered:  {len(ready)}/{len(answers)} ready for auto-fill")
    print(f"  Questions flagged:   {len(review)} need human review")

    answers_json = {qid: ans.model_dump(mode="json") for qid, ans in answers.items()}
    (out_dir / "answers.json").write_text(
        json.dumps(answers_json, indent=2, default=str), encoding="utf-8"
    )

    ai_review_results: dict[str, dict] = {}
    if use_ai_review and ai_client is not None:
        print("\n=== AI Review — Strict validation pass ===")
        ai_review_results = run_ai_review(answers, registry, ai_client)
        confirmed = sum(1 for item in ai_review_results.values() if item.get("status") == "confirmed")
        suggested = sum(1 for item in ai_review_results.values() if item.get("status") == "suggest_change")
        insufficient = sum(1 for item in ai_review_results.values() if item.get("status") == "insufficient_evidence")
        invalid = sum(
            1
            for item in ai_review_results.values()
            if item.get("status") in {"invalid_json", "invalid_quote"}
        )
        print(f"  Reviewed answers: {len(ai_review_results)}")
        print(f"  Confirmed:        {confirmed}")
        print(f"  Suggested change: {suggested}")
        print(f"  Insufficient:     {insufficient}")
        print(f"  Invalid AI output:{invalid}")
        (out_dir / "ai_review.json").write_text(
            json.dumps(ai_review_results, indent=2, default=str), encoding="utf-8"
        )

    print("\n=== Brain D — Building TriConvey action plan ===")
    plan = None
    if yaml_dir.exists():
        plan = build_action_plan(answers, yaml_dir)
        auto_actions = [a for a in plan.actions if a.action != "skip"]
        skip_actions = [a for a in plan.actions if a.action == "skip"]
        print(f"  Auto-fill actions:  {len(auto_actions)}")
        print(f"  Skip/review gates:  {len(skip_actions)}")
        print(f"  Review gate needed: {plan.review_gate_required}")
        (out_dir / "action_plan.json").write_text(
            plan.model_dump_json(indent=2), encoding="utf-8"
        )
    else:
        print(f"  [SKIP] YAML tab dir not found: {yaml_dir}")
        print("         action_plan.json not written")

    write_summary(answers, out_dir)

    print(f"\n=== Output written to {out_dir.resolve()} ===")
    print(f"  facts.json       — {total_facts} facts from {len(doc_paths)} document(s)")
    print(f"  answers.json     — {len(answers)} question answers")
    if use_ai_review and ai_client is not None:
        print(f"  ai_review.json   — {len(ai_review_results)} strict AI review items")
    if plan is not None:
        print(f"  action_plan.json — {len(plan.actions)} form actions")
    print("  summary.txt      — human-readable review checklist")
