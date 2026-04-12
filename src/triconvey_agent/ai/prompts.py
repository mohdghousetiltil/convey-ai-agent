from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_review_prompt(field_key: str, value: Any, snippet: str) -> str:
    return f"""
You are reviewing extracted legal/property form data.

Rules:
- Do not invent facts.
- Only use the evidence snippet.
- Return JSON only.
- If evidence is incomplete, say requires_review=true.
- Keep suggested_value null if unsupported.

Field key:
{field_key}

Current extracted value:
{json.dumps(value, ensure_ascii=False)}

Evidence snippet:
{json.dumps(snippet, ensure_ascii=False)}

Return JSON:
{{
  "field_key": "...",
  "suggested_value": ...,
  "confidence": 0.0,
  "reason": "...",
  "requires_review": true
}}
""".strip()


def build_normalization_prompt(
    final_output: dict[str, Any], baseline_normalized: dict[str, Any], target_schema: dict[str, Any] | None = None
) -> str:
    schema = target_schema
    if schema is None:
        schema_path = Path("normalize_schema.json")
        if schema_path.exists():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        else:
            schema = {}

    return f"""
You are normalizing extracted conveyancing matter data into strict legal/form meanings.

Rules:
- Do not invent file existence.
- A title diagram reference is not proof a standalone plan file is attached.
- Bottled gas is not mains gas supply.
- Tank water is not mains water supply.
- Septic tank is not sewerage.
- For "tick if NOT connected" fields, only output true when definitely not connected; otherwise null.
- Preserve confirmed uploaded files from the baseline normalized output.
- Return JSON only.
- Prefer null over weak inference.
- Keep referenced-only title plans out of confirmed attachments unless an uploaded file proves they exist.

Raw extracted output:
{json.dumps(final_output, ensure_ascii=False)}

Baseline normalized output:
{json.dumps(baseline_normalized, ensure_ascii=False)}

Target schema:
{json.dumps(schema, ensure_ascii=False)}

Return JSON:
{{
  "artifact_version": "1.0",
  "attachments_normalized": [],
  "services_normalized": {{
    "electricity_supply_not_connected": null,
    "gas_supply_not_connected": null,
    "water_supply_not_connected": null,
    "sewerage_not_connected": null,
    "telephone_not_connected": null,
    "alternative_services": {{
      "bottled_gas": null,
      "tank_water": null,
      "septic_tank": null
    }},
    "reasoning_notes": []
  }},
  "review_flags": [],
  "ai_notes": []
}}
""".strip()


def build_services_normalization_prompt(evidence_packet: dict[str, Any], baseline_services: dict[str, Any]) -> str:
    return f"""
You are normalizing conveyancing service connection evidence for Sec 32 form filling.

Task:
- Decide only whether each Sec 32 "NOT connected" service checkbox should be true or null.
- Never output false.
- Preserve alternative services separately.

Rules:
- Mains electricity connected does NOT mean "not connected" true; it should usually stay null.
- Bottled gas is not mains gas supply.
- Tank water is not mains water supply.
- Septic tank is not sewerage.
- Telephone stays null unless there is explicit evidence.
- If uncertain, output null.
- Return strict JSON only.

Evidence packet:
{json.dumps(evidence_packet, ensure_ascii=False)}

Baseline services:
{json.dumps(baseline_services, ensure_ascii=False)}

Return JSON:
{{
  "electricity_supply_not_connected": null,
  "gas_supply_not_connected": null,
  "water_supply_not_connected": null,
  "sewerage_not_connected": null,
  "telephone_not_connected": null,
  "alternative_services": {{
    "bottled_gas": null,
    "tank_water": null,
    "septic_tank": null
  }},
  "reasoning_notes": []
}}
""".strip()


def build_attachments_normalization_prompt(evidence_packet: dict[str, Any], baseline_attachments: list[dict[str, Any]]) -> str:
    return f"""
You are normalizing conveyancing attachment evidence for Sec 32 form filling.

Task:
- Classify each item conservatively as one of:
  - uploaded_file
  - title_reference_only
  - placeholder
  - inferred
- Set exists_as_file=true only when an uploaded/source file actually supports it.
- Do not convert title references into confirmed attachments.

Rules:
- A diagram_location or title reference is not proof a standalone plan document is attached.
- Preserve confirmed uploaded files from the baseline unless contradicted.
- Prefer null or review_note over weak inference.
- Return strict JSON only.

Evidence packet:
{json.dumps(evidence_packet, ensure_ascii=False)}

Baseline attachments:
{json.dumps(baseline_attachments, ensure_ascii=False)}

Return JSON:
[
  {{
    "doc_type": "",
    "display_text": "",
    "exists_as_file": false,
    "source": "title_reference_only",
    "source_file": null,
    "confidence": 0.0,
    "reference_number": null,
    "review_note": ""
  }}
]
""".strip()
