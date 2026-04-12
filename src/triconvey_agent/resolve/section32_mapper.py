from __future__ import annotations

from copy import deepcopy

from triconvey_agent.catalog.sec32_catalog import SECTION32_QUESTION_CATALOG
from triconvey_agent.schemas.extracted import FieldValue, FinalExtraction, Section32Questions


def _truthy_value(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return any(item not in (None, "", [], {}) for item in value.values())
    if value in (None, "", [], {}):
        return None
    return None


def _as_question_answer_field(field: FieldValue, bool_value: bool) -> FieldValue:
    cloned = deepcopy(field)
    cloned.value = bool_value
    cloned.extractor = "section32_mapper"
    return cloned


def _lookup_field(extraction: FinalExtraction, section: str, key: str) -> FieldValue | None:
    container = getattr(extraction, section, None)
    if not isinstance(container, dict):
        return None
    return container.get(key)


def map_to_section32_questions(extraction: FinalExtraction) -> Section32Questions:
    result = Section32Questions()

    for item in SECTION32_QUESTION_CATALOG:
        if item.status == "skip":
            continue

        if not item.canonical_key:
            continue

        field = _lookup_field(extraction, item.section, item.canonical_key)
        if field is None or field.value is None:
            result.unanswered_or_not_found[item.question] = FieldValue(
                value=None,
                confidence=0.0,
                extractor="section32_mapper",
                normalized_key=item.canonical_key,
                evidence=[],
                conflicts=[],
            )
            continue

        derived_bool = _truthy_value(field.value)

        if derived_bool is True:
            result.yes_questions[item.question] = _as_question_answer_field(field, True)
            continue

        if derived_bool is False:
            result.no_questions[item.question] = _as_question_answer_field(field, False)
            continue

        if item.status == "required_if_found":
            result.yes_questions[item.question] = _as_question_answer_field(field, True)
            continue

        result.unanswered_or_not_found[item.question] = field

    return result
