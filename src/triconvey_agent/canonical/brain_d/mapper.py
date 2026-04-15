"""Brain D — field mapper implementation.

Reads YAML tab files, applies the static FIELD_MAP, and produces a
FormActionPlan that Brain E can execute.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from triconvey_agent.canonical.brain_d.field_map import FIELD_MAP, FieldBinding
from triconvey_agent.normalizers.display_names import normalize_council_display_name
from triconvey_agent.canonical.schemas import (
    AnswerObject,
    AnswerStrategy,
    FormAction,
    FormActionPlan,
)

_NON_EXECUTABLE_QUESTIONS = {
    # Tab 6 field 12 is display-only in the captured TriConvey maps; trying to
    # write "Is attached" was landing in the preceding free-text area.
    "policy_6_due_diligence",
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
        "sec32_3.4_responsible_authority",
    }:
        return normalize_council_display_name(str(payload))
    return payload


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
