from __future__ import annotations

from triconvey_agent.extractors.common import iter_page_text, iter_question_answer_pairs, make_field, normalize_question_key
from triconvey_agent.ingest.normalizer import normalize_scalar
from triconvey_agent.schemas.documents import Document
from triconvey_agent.schemas.extracted import FieldValue

YES_VALUES = {"yes", "true"}
NO_VALUES = {"no", "false"}
UNANSWERED_VALUES = {"unsure", "unknown", "none", "not applicable"}


def _classify_answer(raw_answer: str) -> tuple[str, object]:
    normalized = normalize_scalar(raw_answer)
    if normalized is None:
        return "unanswered", None

    answer = str(normalized).strip().lower()
    if answer in YES_VALUES or answer.startswith("yes"):
        return "yes", True
    if answer in NO_VALUES or answer.startswith("no"):
        return "no", False
    if answer in UNANSWERED_VALUES:
        return "unanswered", normalized
    return "other", normalized


def extract_yes_no_questions(doc: Document) -> dict[str, dict[str, FieldValue]]:
    yes_questions: dict[str, FieldValue] = {}
    no_questions: dict[str, FieldValue] = {}
    unanswered_or_not_found: dict[str, FieldValue] = {}

    for page_number, text in iter_page_text(doc):
        for question, answer in iter_question_answer_pairs(text):
            bucket, value = _classify_answer(answer)
            if bucket == "other":
                continue

            field = make_field(
                value=value,
                document=doc,
                extractor="yes_no_extractor",
                normalized_key=normalize_question_key(question),
                page=page_number,
                snippet=f"{question}\n{answer}",
                label=question,
                confidence=0.97 if bucket in {"yes", "no"} else 0.8,
            )

            if bucket == "yes":
                yes_questions[question] = field
            elif bucket == "no":
                no_questions[question] = field
            else:
                unanswered_or_not_found[question] = field

    return {
        "yes_questions": yes_questions,
        "no_questions": no_questions,
        "unanswered_or_not_found": unanswered_or_not_found,
    }
