from __future__ import annotations

from pathlib import Path

from triconvey_agent.canonical.questions.loader import load_question_registry


def write_summary(answers: dict, out_dir: Path) -> None:
    lines = ["TRICONVEY AGENT - ANSWER SUMMARY", "=" * 60, ""]

    tabs: dict[str, list] = {}
    registry = load_question_registry()
    for qid, ans in answers.items():
        q = registry.get(qid)
        tab = q.tab if q else "Unknown"
        tabs.setdefault(tab, []).append((qid, ans))

    for tab, items in sorted(tabs.items()):
        lines.append(f"--- {tab} ---")
        for _qid, ans in items:
            label = ans.question_label
            if ans.needs_review:
                status = "! REVIEW"
                detail = f"[{', '.join(ans.review_reasons[:1])}]"
            else:
                status = "* AUTO  "
                detail = ""
            value_str = repr(ans.value) if ans.value is not None else "—"
            lines.append(f"  {status:10}  {label:<50}  {value_str}  {detail}")
        lines.append("")

    summary = "\n".join(lines)
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary)
