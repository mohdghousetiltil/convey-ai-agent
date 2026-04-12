"""Targeted AI confirmation for Step C.

After deterministic mapping (Step B), a small focused set of fields is sent
to GPT-4o for confirmation or correction.  The AI does NOT fill nulls — it
reviews what the rules already decided and flags mistakes.

Fields sent for confirmation:
  - All service connection fields  (trickiest — inferred from alternative services)
  - Key yes/no disclosures         (bushfire, GAIC — high-stakes, worth confirming)
  - Rate authority names           (council name normalisation is fragile)
  - Any answered field whose source evidence has confidence < CONFIDENCE_THRESHOLD

Fields NOT sent:
  - Questions tagged out_of_scope
  - Null questions (no answer to confirm)
  - Policy override fields (client policy is intentional, not data-driven)

The AI returns "confirm" or "correct: <new_value>" per field.  Only corrections
are applied.  If the AI is uncertain it should confirm the existing answer —
the prompt enforces this.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from triconvey_agent.ai.client import AIClient

# Confidence below this threshold triggers AI review of that field.
CONFIDENCE_THRESHOLD = 0.75

# question_id → paths in final_output that provided the evidence for this answer.
# Used to build the evidence packet sent to the AI.
CONFIRMATION_TARGETS: dict[str, list[str]] = {
    # Services — evidence paths match what the vendor form extractor actually produces.
    # mains_* keys are produced when the PDF uses "Mains Electricity" etc. labels.
    # alternative service keys (bottled_gas, septic_tank, tank_water) indicate
    # the property uses an alternative, implying the corresponding mains is absent.
    "s32_4.service_electricity_not_connected": [
        "services_connected.mains_electricity",
    ],
    "s32_4.service_gas_not_connected": [
        "services_connected.mains_gas",
        "services_connected.bottled_gas",   # alt service = no mains gas
    ],
    "s32_4.service_water_not_connected": [
        "services_connected.mains_water",
        "services_connected.tank_water",    # alt service = no mains water
    ],
    "s32_4.service_sewerage_not_connected": [
        "services_connected.mains_sewerage",  # produced when PDF says "Mains Sewerage"
        "services_connected.sewerage",         # produced when PDF says plain "Sewerage"
        "services_connected.septic_tank",      # alt service = no mains sewerage
    ],
    "s32_4.service_telephone_not_connected": [
        "services_connected.nbn",       # produced when PDF says "NBN" or "VOIP phone via NBN"
        "services_connected.telephone",
    ],
    # Key yes/no disclosures
    "s32_2.bushfire_prone_area": [
        "planning_building_permits.bushfire_prone_area",
    ],
    "s32_4.gaic_applies": [
        "planning_building_permits.gaic_trigger",
    ],
    # Rate authority names
    "s32_1.outgoing_1_authority": [
        "rates_taxes_charges.council",
        "rates_taxes_charges.council_rates",
    ],
    "s32_1.outgoing_2_authority": [
        "rates_taxes_charges.water_authority",
        "rates_taxes_charges.water_rates",
    ],
}


# ---------------------------------------------------------------------------
# Evidence extraction
# ---------------------------------------------------------------------------

def _get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _extract_evidence(final_output: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    """Pull value + confidence + snippet from final_output for each evidence path."""
    evidence: dict[str, Any] = {}
    for path in paths:
        field = _get(final_output, path)
        if field is None:
            continue
        key = path.split(".")[-1]
        evidence[key] = {
            "value": field.get("value") if isinstance(field, dict) else field,
            "confidence": field.get("confidence") if isinstance(field, dict) else None,
            "snippet": (field.get("evidence") or [{}])[0].get("snippet") if isinstance(field, dict) else None,
        }
    return evidence


def _low_confidence_answered(
    answered_template: dict[str, Any],
    final_output: dict[str, Any],
    skip_ids: set[str],
) -> list[dict[str, Any]]:
    """Find answered questions whose source data had confidence below threshold."""
    low_conf: list[dict[str, Any]] = []

    # Build a reverse map: normalized_key → paths in final_output
    # We check a few core sections for low-confidence values.
    sections_to_check = [
        "rates_taxes_charges",
        "planning_building_permits",
        "services_connected",
        "vic_title_extract",
    ]
    low_conf_keys: set[str] = set()
    for section in sections_to_check:
        section_data = final_output.get(section, {})
        for field_key, field in section_data.items():
            if not isinstance(field, dict):
                continue
            if field.get("confidence", 1.0) < CONFIDENCE_THRESHOLD:
                low_conf_keys.add(field_key)

    for tab in answered_template.get("tabs", []):
        for question in tab.get("questions", []):
            qid = question["question_id"]
            if qid in skip_ids:
                continue
            if question.get("answer") is None:
                continue
            if question.get("skip_reason") == "out_of_scope":
                continue
            # Check if any word in the question_id matches a low-confidence field
            short_key = qid.split(".")[-1]
            if any(short_key in lck or lck in short_key for lck in low_conf_keys):
                low_conf.append({
                    "question_id": qid,
                    "label": question["label"],
                    "control_type": question["control_type"],
                    "current_answer": question["answer"],
                    "inverted": question.get("inverted", False),
                    "evidence": {"low_confidence_source": True},
                })

    return low_conf


# ---------------------------------------------------------------------------
# Confirmation target collection
# ---------------------------------------------------------------------------

def collect_confirmation_targets(
    answered_template: dict[str, Any],
    final_output: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the list of fields to send to GPT-4o for confirmation.

    Includes:
    - All CONFIRMATION_TARGETS that have a non-null answer in the template
    - Any answered field whose source evidence had confidence < CONFIDENCE_THRESHOLD
    """
    question_lookup: dict[str, dict[str, Any]] = {}
    for tab in answered_template.get("tabs", []):
        for q in tab.get("questions", []):
            question_lookup[q["question_id"]] = q

    targets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # Primary targets — always included if answered OR if there is evidence to fill from.
    # Service fields are included even when null so the AI can gap-fill using raw evidence.
    service_target_ids = {qid for qid in CONFIRMATION_TARGETS if "service_" in qid}

    for question_id, evidence_paths in CONFIRMATION_TARGETS.items():
        question = question_lookup.get(question_id)
        if question is None:
            continue
        if question.get("skip_reason") == "out_of_scope":
            continue
        if question.get("answer") is None and question_id not in service_target_ids:
            continue  # non-service null fields: nothing to confirm

        evidence = _extract_evidence(final_output, evidence_paths)
        targets.append({
            "question_id": question_id,
            "label": question["label"],
            "control_type": question["control_type"],
            "current_answer": question["answer"],
            "inverted": question.get("inverted", False),
            "evidence": evidence,
        })
        seen_ids.add(question_id)

    # Secondary targets — low-confidence answered fields
    for item in _low_confidence_answered(answered_template, final_output, skip_ids=seen_ids):
        targets.append(item)
        seen_ids.add(item["question_id"])

    return targets


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_confirmation_prompt(
    targets: list[dict[str, Any]],
    simplified_data: dict[str, Any],
) -> str:
    return f"""You are reviewing a Victorian Section 32 vendor disclosure form (Australian conveyancing).

CONTEXT — full extracted property data:
{json.dumps(simplified_data, ensure_ascii=False, indent=2)}

FIELDS TO REVIEW:
{json.dumps(targets, ensure_ascii=False, indent=2)}

RULES:
1. If current_answer is NOT null: return "confirm" if correct, or "correct" + new value if wrong.
2. If current_answer IS null: return "fill" + value if you can determine the answer from evidence, or "skip" if uncertain.
3. If inverted=true: the checkbox means "NOT connected/present" — true=absent, false=present.
   Example: service_sewerage_not_connected with mains_sewerage evidence=true → the box should be FALSE (service IS connected).
4. For service fields: use the evidence values directly.
   - mains_electricity/mains_gas/mains_water/mains_sewerage/nbn = true → service IS connected → field value = false
   - mains_* = false AND no alternative = true → service NOT connected → field value = true
   - bottled_gas/tank_water/septic_tank = true → that mains service is NOT connected → field value = true
5. For authority names: correct only if clearly wrong (e.g. abbreviation vs full legal name).
6. If uncertain: confirm or skip — a wrong confirm is safer than a wrong correction.
7. For CheckBox: value must be true, false, or null only.
8. For Edit: value must be a string or null.

Return ONLY a valid JSON object (no markdown, no explanation):
{{
  "question_id": {{"action": "confirm"}},
  "question_id": {{"action": "correct", "value": <new_value>}},
  "question_id": {{"action": "fill", "value": <value>}},
  "question_id": {{"action": "skip"}},
  ...
}}""".strip()


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"AI response did not contain valid JSON:\n{text[:300]}")
    return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# Apply AI corrections
# ---------------------------------------------------------------------------

def _simplify_section(section: Any) -> Any:
    if isinstance(section, dict):
        if "value" in section:
            return section["value"]
        return {k: _simplify_section(v) for k, v in section.items()}
    if isinstance(section, list):
        return [_simplify_section(item) for item in section]
    return section


def simplify_final_output(final_output: dict[str, Any]) -> dict[str, Any]:
    skip = {"source_summary", "review_summary", "skipped_sections", "ai_suggestions", "additional_titles"}
    return {k: _simplify_section(v) for k, v in final_output.items() if k not in skip}


def apply_ai_corrections(
    answered_template: dict[str, Any],
    ai_decisions: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Apply GPT-4o decisions to the answered template.

    Actions:
        confirm — no-op (keep existing answer)
        correct — overwrite existing answer with AI value
        fill    — set answer on a previously null field
        skip    — no-op (AI had insufficient evidence)

    Returns (updated_template, list_of_changed_question_ids).
    """
    changed: list[str] = []
    for tab in answered_template.get("tabs", []):
        for question in tab.get("questions", []):
            qid = question["question_id"]
            decision = ai_decisions.get(qid)
            if not isinstance(decision, dict):
                continue
            action = decision.get("action")
            if action in ("correct", "fill") and "value" in decision:
                question["answer"] = decision["value"]
                changed.append(qid)
    return answered_template, changed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_ai_confirmation(
    final_output: dict[str, Any],
    answered_template: dict[str, Any],
    client: AIClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run GPT-4o confirmation pass on targeted fields.

    Returns:
        (updated_template, ai_report)
    """
    targets = collect_confirmation_targets(answered_template, final_output)

    if not targets:
        return answered_template, {
            "targets_sent": 0,
            "confirmed": 0,
            "corrected": 0,
            "corrected_ids": [],
            "error": None,
        }

    simplified = simplify_final_output(final_output)
    prompt = build_confirmation_prompt(targets, simplified)

    ai_decisions: dict[str, Any] = {}
    error_msg: str | None = None

    try:
        response = client.complete(prompt)
        raw = _extract_json(response.raw_text)
        if isinstance(raw, dict):
            ai_decisions = raw
        else:
            error_msg = "AI returned non-dict JSON — skipping confirmation."
    except Exception as exc:
        error_msg = str(exc)

    updated = deepcopy(answered_template)
    corrected_ids: list[str] = []
    if ai_decisions:
        updated, corrected_ids = apply_ai_corrections(updated, ai_decisions)

    confirmed = sum(1 for d in ai_decisions.values() if isinstance(d, dict) and d.get("action") == "confirm")
    filled = sum(1 for qid in corrected_ids if ai_decisions.get(qid, {}).get("action") == "fill")
    corrected = sum(1 for qid in corrected_ids if ai_decisions.get(qid, {}).get("action") == "correct")

    return updated, {
        "targets_sent": len(targets),
        "confirmed": confirmed,
        "filled": filled,
        "corrected": corrected,
        "changed_ids": corrected_ids,
        "corrected_ids": corrected_ids,  # kept for backward compat
        "error": error_msg,
    }
