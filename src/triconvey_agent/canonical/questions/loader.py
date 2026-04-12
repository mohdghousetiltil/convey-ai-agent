"""YAML → Pydantic Question loader for the question registry.

The registry is human-editable YAML so a paralegal can review and tweak
question routing without touching Python. The loader validates every entry
into a Question pydantic model and raises clear errors if anything is off.

YAML format
-----------

    questions:
      - id: sec32_8_electricity_not_connected
        tab: "Sec. 32 (4)"
        label: "8. Services - Electricity supply (tick if NOT connected)"
        fact_paths:
          - services.electricity.connected
        answer_strategy: deterministic
        expected_type: bool
        description: |
          Tick the box only when electricity is NOT connected.
          Vendor form is the primary source.

      - id: sec32_3.4_planning_zone
        tab: "Sec. 32 (2)"
        label: "3.4 Planning zone"
        fact_paths:
          - planning.zone
        answer_strategy: deterministic
        options:
          - "GRZ - General Residential Zone"
          - "NRZ - Neighbourhood Residential Zone"
          - "..."
        expected_type: string

The loader returns dict[str, Question] keyed by id. The id is required
to be unique; duplicates raise QuestionLoadError.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from triconvey_agent.canonical.schemas import Question


class QuestionLoadError(ValueError):
    """Raised when the questions.yaml file fails to validate."""


def _format_validation_error(question_index: int, exc: ValidationError) -> str:
    lines = [f"questions[{question_index}] failed validation:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_questions_from_yaml(path: str | Path) -> dict[str, Question]:
    """Load and validate a question registry from a YAML file.

    Args:
        path: Path to questions.yaml.

    Returns:
        dict mapping question_id → Question (insertion order preserved).

    Raises:
        QuestionLoadError: file missing, malformed YAML, validation error,
                           or duplicate ids.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise QuestionLoadError(f"Question registry not found: {file_path}")

    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QuestionLoadError(f"Could not read {file_path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise QuestionLoadError(f"Invalid YAML in {file_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise QuestionLoadError(
            f"Top-level YAML in {file_path} must be a mapping with a 'questions' key, "
            f"got {type(data).__name__}"
        )

    raw_questions: Any = data.get("questions")
    if not isinstance(raw_questions, list):
        raise QuestionLoadError(
            f"'questions' key in {file_path} must be a list, got {type(raw_questions).__name__}"
        )

    registry: dict[str, Question] = {}
    for index, raw_q in enumerate(raw_questions):
        if not isinstance(raw_q, dict):
            raise QuestionLoadError(
                f"questions[{index}] must be a mapping, got {type(raw_q).__name__}"
            )
        try:
            question = Question(**raw_q)
        except ValidationError as exc:
            raise QuestionLoadError(_format_validation_error(index, exc)) from exc

        if question.id in registry:
            raise QuestionLoadError(
                f"Duplicate question id '{question.id}' at questions[{index}] "
                f"(first seen at id={question.id})"
            )
        registry[question.id] = question

    return registry


def load_question_registry(
    project_root: str | Path | None = None,
) -> dict[str, Question]:
    """Convenience: load the bundled registry.yaml from this package.

    If `project_root` is given, looks for `<project_root>/questions.yaml`
    first and falls back to the bundled registry.
    """
    if project_root is not None:
        custom = Path(project_root) / "questions.yaml"
        if custom.exists():
            return load_questions_from_yaml(custom)

    bundled = Path(__file__).parent / "registry.yaml"
    return load_questions_from_yaml(bundled)
