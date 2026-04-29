from __future__ import annotations

import json
from pathlib import Path


def test_find_proposed_changes_handles_dict_answers(tmp_path: Path, monkeypatch) -> None:
    # Import inside test so monkeypatches affect module-level refs.
    import triconvey_agent.corpus.incremental as inc

    # Create an answers.json in the on-disk (dict) shape used by the main pipeline.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "answers.json").write_text(
        json.dumps(
            {
                "q1": {"question_id": "q1", "value": "old"},
            }
        ),
        encoding="utf-8",
    )

    class _Q:
        def __init__(self, question_id: str) -> None:
            self.id = question_id
            self.fact_paths = ["building.permit"]

    class _A:
        def __init__(self, qid: str, value: object) -> None:
            self.question_id = qid
            self.value = value
            self.confidence = 0.9
            self.evidence = []
            self.label = qid
            self.tab = ""

    # Registry has one relevant question; answer_all_questions returns dict (the bug trigger).
    monkeypatch.setattr(inc, "load_question_registry", lambda: {"q1": _Q("q1")})
    monkeypatch.setattr(inc, "answer_all_questions", lambda *args, **kwargs: {"q1": _A("q1", "new")})

    # Avoid needing real fact store logic: prefixes are enough to enter the codepath.
    inc._DOC_TYPE_FACT_PREFIXES["building_permit"] = ["building."]

    changes = inc._find_proposed_changes(
        run_dir=run_dir,
        doc_path=tmp_path / "doc.pdf",
        doc_type="building_permit",
        single_store=None,
        corpus_entry=None,
    )
    assert len(changes) == 1
    assert changes[0]["question_id"] == "q1"
    assert changes[0]["current_value"] == "old"
    assert changes[0]["proposed_value"] == "new"

