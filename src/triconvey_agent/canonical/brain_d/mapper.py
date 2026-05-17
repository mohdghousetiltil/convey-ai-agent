"""Brain D — field mapper implementation.

Reads YAML tab files, applies the static FIELD_MAP, and produces a
FormActionPlan that Brain E can execute.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from triconvey_agent.canonical.brain_d.field_map import FIELD_MAP, FieldBinding
from triconvey_agent.normalizers.display_names import (
    normalize_council_display_name,
    normalize_water_authority_display_name,
    WATER_AUTHORITY_DISPLAY_NAME_MAP,
)
from triconvey_agent.canonical.schemas import (
    AnswerObject,
    AnswerStrategy,
    FormAction,
    FormActionPlan,
)

_NON_EXECUTABLE_QUESTIONS = {
    # Firm preference: when there is no OC, leave the field blank rather than
    # auto-marking "Owners Corporation is inactive".
    "sec32_oc_inactive",
}

# ---------------------------------------------------------------------------
# YAML tab loading
# ---------------------------------------------------------------------------


def _load_yaml_tabs(yaml_dir: str | Path) -> dict[str, list[dict]]:
    """Load all tab_sec_32_*.yaml files and index by tab_name."""
    tabs: dict[str, list[dict]] = {}
    yaml_dir = Path(yaml_dir)
    for path in sorted(yaml_dir.glob("tab_sec_32_*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            tab_name = data.get("tab_name", path.stem)
            tabs[tab_name] = data.get("fields", [])
        except Exception:
            pass
    return tabs


# ---------------------------------------------------------------------------
# Value adapters
# ---------------------------------------------------------------------------


def _adapt_value(raw: Any, adapter: str) -> Any:
    if raw is None:
        return None
    if adapter == "direct":
        return raw
    if adapter == "str":
        return str(raw)
    if adapter == "bool_tick":
        if isinstance(raw, bool):
            return raw
        return bool(raw)
    if adapter == "dollar_strip":
        s = str(raw).strip()
        return s.lstrip("$").strip() if s.startswith("$") else s
    return raw


def _normalize_payload(question_id: str, payload: Any) -> Any:
    if payload is None:
        return None
    if question_id in {
        "sec32_1.1_outgoing_1_authority",
        "sec32_1.1_outgoing_2_authority",
        "sec32_1.1_outgoing_3_authority",
        "sec32_1.1_outgoing_4_authority",
        "sec32_3.4_responsible_authority",
    }:
        name = str(payload)
        # Unit water authority format "Unit - N - {authority} - annually" — pass through as-is.
        if name.strip().lower().startswith("unit - "):
            return name.strip()
        # Water authorities take priority — check before applying council suffix logic.
        if name.strip().lower() in WATER_AUTHORITY_DISPLAY_NAME_MAP:
            return normalize_water_authority_display_name(name)
        return normalize_council_display_name(name)
    return payload


def _source_kind(answer: AnswerObject, question_id: str) -> str:
    if question_id.startswith("policy_"):
        return "policy_default"
    if answer.answer_strategy == AnswerStrategy.POLICY_DEFAULT:
        return "policy_default"
    if answer.answer_strategy == AnswerStrategy.GROUNDED_AI:
        return "grounded_ai"
    facts = answer.facts_used or []
    if any("policy_computed" in fact or "policy_attachments" in fact for fact in facts):
        return "computed_policy"
    if facts:
        return "extracted_fact"
    return "unknown"


def _intent_category(binding: FieldBinding, question_id: str, source_kind: str) -> str:
    if binding.action == "skip":
        return "skip"
    if binding.action == "select_dropdown":
        return "selection"
    if binding.action == "set_checkbox" and source_kind == "policy_default":
        return "policy_tick"
    if source_kind == "computed_policy":
        return "derived_entry"
    if binding.action == "set_text" and source_kind == "policy_default":
        return "derived_entry"
    return "exact_entry"


def _intent_summary(question_id: str, answer: AnswerObject, binding: FieldBinding, payload: Any, source_kind: str) -> str:
    label = answer.question_label or question_id
    if binding.action == "set_checkbox" and source_kind == "policy_default":
        return f"Firm policy requires ticking '{label}'."
    if source_kind == "computed_policy":
        return f"Computed policy output is being entered for '{label}'."
    if binding.action == "select_dropdown":
        return f"Choosing the mapped option for '{label}'."
    if binding.action == "set_text":
        if source_kind == "policy_default":
            return f"Standard firm wording is being entered for '{label}'."
        return f"Entering the extracted value for '{label}': {payload!s}"
    if binding.action == "set_checkbox":
        return f"Applying the extracted true/false result for '{label}'."
    return f"Handling '{label}'."


# ---------------------------------------------------------------------------
# Field finder
# ---------------------------------------------------------------------------

_TOP_TOLERANCE = 5
_LEFT_TOLERANCE = 5


def _field_matches(field: dict, binding: FieldBinding) -> bool:
    if field.get("control_type") != binding.control_type:
        return False
    if binding.match_name is not None:
        if field.get("name") != binding.match_name:
            return False
    if binding.match_label is not None:
        if field.get("nearby_label") != binding.match_label:
            return False
    pos = field.get("position") or {}
    if binding.match_top is not None:
        top = pos.get("top")
        if top is None or abs(top - binding.match_top) > _TOP_TOLERANCE:
            return False
    if binding.match_left_min is not None:
        left = pos.get("left")
        if left is None or left < binding.match_left_min - _LEFT_TOLERANCE:
            return False
    if binding.match_left_max is not None:
        left = pos.get("left")
        if left is None or left > binding.match_left_max + _LEFT_TOLERANCE:
            return False
    return True


def _find_field(fields: list[dict], binding: FieldBinding) -> dict | None:
    for f in fields:
        if _field_matches(f, binding):
            return f
    return None


def _field_id(tab_name: str, field: dict) -> str:
    """Build a stable string ID for a field — used as FormAction.field_id."""
    pos = field.get("position") or {}
    name = field.get("name") or field.get("nearby_label") or "unnamed"
    top = pos.get("top", "?")
    left = pos.get("left", "?")
    return f"{tab_name}::{field.get('control_type','')}::{name}::t{top}l{left}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_action_plan(
    answers: dict[str, AnswerObject],
    yaml_dir: str | Path,
) -> FormActionPlan:
    """Map AnswerObjects to FormActions using the static FIELD_MAP.

    Parameters
    ----------
    answers   : dict[question_id → AnswerObject] from answer_all_questions()
    yaml_dir  : directory containing tab_sec_32_*.yaml files

    Returns
    -------
    FormActionPlan with one FormAction per mapped field.
    Questions with no binding in FIELD_MAP are skipped (internal/vendor
    questions that don't correspond to a visible form field).
    """
    tabs = _load_yaml_tabs(yaml_dir)
    actions: list[FormAction] = []
    review_gate_required = False

    for question_id, answer in answers.items():
        if question_id in _NON_EXECUTABLE_QUESTIONS:
            continue
        bindings = FIELD_MAP.get(question_id)
        if not bindings:
            continue  # internal question or not yet mapped

        for binding in bindings:
            fields = tabs.get(binding.tab_name, [])
            matched_field = _find_field(fields, binding)

            if matched_field is None:
                # Field not found in YAML — emit a skip action so Brain E
                # can log the gap rather than silently missing it.
                actions.append(
                    FormAction(
                        question_id=question_id,
                        field_id=f"{binding.tab_name}::NOTFOUND::{binding.match_name or binding.match_label}",
                        action="skip",
                        payload=None,
                        needs_review_first=True,
                        source_answer_confidence=answer.confidence,
                    )
                )
                continue

            fid = _field_id(binding.tab_name, matched_field)

            if answer.needs_review:
                # Answer is uncertain — emit the action but gate on review.
                review_gate_required = True
                actions.append(
                    FormAction(
                        question_id=question_id,
                        field_id=fid,
                        action="skip",
                        payload=None,
                        needs_review_first=True,
                        source_answer_confidence=answer.confidence,
                    )
                )
                continue

            if answer.value is None:
                actions.append(
                    FormAction(
                        question_id=question_id,
                        field_id=fid,
                        action="skip",
                        payload=None,
                        needs_review_first=False,
                        source_answer_confidence=answer.confidence,
                    )
                )
                continue

            payload = _adapt_value(answer.value, binding.value_adapter)
            payload = _normalize_payload(question_id, payload)
            expected = payload  # Brain E compares actual field value to this

            actions.append(
                FormAction(
                    question_id=question_id,
                    field_id=fid,
                    action=binding.action,
                    payload=payload,
                    expected_after=expected,
                    needs_review_first=False,
                    source_answer_confidence=answer.confidence,
                )
            )

    return FormActionPlan(
        actions=actions,
        review_gate_required=review_gate_required,
    )
