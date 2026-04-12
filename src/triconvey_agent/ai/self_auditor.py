"""AI self-auditor — checks its own extraction against source text.

After the property agent builds a knowledge base, the self-auditor:
  1. Picks fields where confidence < 0.80 or quote_verified=False
  2. Sends those specific fields + their raw document context to GPT-4o
  3. GPT-4o either confirms, corrects, or flags each field
  4. Corrections are written back to the knowledge base

This is a targeted, cheap second pass — only re-checks uncertain fields.
Typical cost: 5-15 fields × small prompt = < $0.05 per matter.
"""
from __future__ import annotations

import json
import re
from typing import Any

from triconvey_agent.ai.client import AIClient
from triconvey_agent.ai.property_agent import PropertyKnowledgeBase
from triconvey_agent.schemas.documents import Document

_MAX_SNIPPET_CHARS = 3_000
_CONFIDENCE_THRESHOLD = 0.80

_AUDIT_PROMPT = """\
You are auditing AI-extracted fields from property documents.
For each field below you will see:
  - The field name and current extracted value
  - The document text snippet where this value should be found
  - The quote the AI claimed (which may be wrong or missing)

Your job: verify each field and return corrections.

FIELDS TO AUDIT:
{fields_json}

DOCUMENT SNIPPETS:
{snippets}

TASK — Return ONLY this JSON (no markdown):
{{
  "audited": [
    {{
      "field": "<section.key>",
      "action": "confirm" | "correct" | "reject",
      "corrected_value": null,
      "corrected_quote": null,
      "confidence": 0.0,
      "reason": "<brief explanation>"
    }}
  ]
}}

RULES:
- "confirm"  → current value is correct; provide the real verbatim quote if missing.
- "correct"  → value is wrong; provide corrected_value + corrected_quote (verbatim).
- "reject"   → field cannot be verified; set corrected_value = null.
- corrected_quote MUST be a verbatim substring from the document snippet above.
- NEVER invent values. Reject rather than guess.
"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(_strip_fences(text))
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        raise ValueError(f"No JSON found in audit response: {text[:200]}")


def _collect_uncertain_fields(kb: PropertyKnowledgeBase) -> list[dict[str, Any]]:
    """Collect fields that need re-verification (low confidence or unverified quote)."""
    uncertain: list[dict[str, Any]] = []
    for section in ("property", "vendor", "services", "rates", "planning"):
        for field_key, field in kb.raw.get(section, {}).items():
            if not isinstance(field, dict):
                continue
            if field.get("value") is None:
                continue
            conf = float(field.get("confidence", 0.0))
            verified = bool(field.get("quote_verified", False))
            if conf < _CONFIDENCE_THRESHOLD or not verified:
                uncertain.append({
                    "section": section,
                    "key": field_key,
                    "field": f"{section}.{field_key}",
                    "value": field.get("value"),
                    "quote": field.get("quote"),
                    "source_file": field.get("source_file"),
                    "confidence": conf,
                    "quote_verified": verified,
                })
    return uncertain


def _build_snippets(uncertain: list[dict[str, Any]], docs: list[Document]) -> str:
    """For each uncertain field, pull a relevant snippet from its source document."""
    text_map = {doc.filename: (doc.raw_text or doc.normalized_text or "") for doc in docs}
    seen_files: set[str] = set()
    parts: list[str] = []

    for field in uncertain:
        source = field.get("source_file") or ""
        if source in seen_files:
            continue
        seen_files.add(source)
        raw = text_map.get(source, "")
        if not raw:
            continue
        snippet = raw[:_MAX_SNIPPET_CHARS]
        if len(raw) > _MAX_SNIPPET_CHARS:
            snippet += f"\n[... truncated ...]"
        parts.append(f"=== {source} ===\n{snippet}")

    return "\n\n".join(parts)


def _apply_audit_results(kb: PropertyKnowledgeBase, audited: list[dict[str, Any]]) -> PropertyKnowledgeBase:
    """Write audit corrections back into the knowledge base."""
    for item in audited:
        action = item.get("action", "")
        field_path = item.get("field", "")
        if "." not in field_path:
            continue
        section, key = field_path.split(".", 1)
        section_data = kb.raw.get(section, {})
        if key not in section_data:
            continue
        current = section_data[key]
        if not isinstance(current, dict):
            continue

        if action == "confirm":
            # Accept existing value; update quote if auditor found a better one
            new_quote = item.get("corrected_quote") or current.get("quote")
            current["quote"] = new_quote
            current["confidence"] = float(item.get("confidence") or current.get("confidence", 0.0))
            current["quote_verified"] = True  # auditor confirmed it exists
            current["audit_action"] = "confirmed"

        elif action == "correct":
            corrected_value = item.get("corrected_value")
            corrected_quote = item.get("corrected_quote")
            if corrected_value is not None:
                current["value"] = corrected_value
                current["quote"] = corrected_quote
                current["confidence"] = float(item.get("confidence") or 0.75)
                current["quote_verified"] = bool(corrected_quote)
                current["audit_action"] = "corrected"

        elif action == "reject":
            current["value"] = None
            current["quote"] = None
            current["confidence"] = 0.0
            current["quote_verified"] = False
            current["audit_action"] = "rejected"

    return kb


def run_self_audit(
    kb: PropertyKnowledgeBase,
    docs: list[Document],
    client: AIClient,
) -> PropertyKnowledgeBase:
    """Run the self-audit loop on uncertain fields.

    Args:
        kb:     The PropertyKnowledgeBase from run_property_agent().
        docs:   All documents (needed to pull snippets for re-checking).
        client: AIClient for GPT-4o.

    Returns:
        The same kb, mutated in-place with corrections applied.
    """
    uncertain = _collect_uncertain_fields(kb)
    if not uncertain:
        kb.raw["audit_summary"] = {"status": "skipped", "reason": "all fields verified", "fields_checked": 0}
        return kb

    snippets = _build_snippets(uncertain, docs)
    fields_json = json.dumps(
        [{"field": f["field"], "value": f["value"], "quote": f["quote"], "source_file": f["source_file"]}
         for f in uncertain],
        indent=2,
    )

    prompt = _AUDIT_PROMPT.format(fields_json=fields_json, snippets=snippets)

    try:
        response = client.complete(prompt)
        raw = _parse_json(response.raw_text)
        audited = raw.get("audited", [])
        kb = _apply_audit_results(kb, audited)
        confirmed = sum(1 for a in audited if a.get("action") == "confirm")
        corrected = sum(1 for a in audited if a.get("action") == "correct")
        rejected  = sum(1 for a in audited if a.get("action") == "reject")
        kb.raw["audit_summary"] = {
            "status": "completed",
            "fields_checked": len(uncertain),
            "confirmed": confirmed,
            "corrected": corrected,
            "rejected": rejected,
        }
    except Exception as exc:  # noqa: BLE001
        kb.raw["audit_summary"] = {"status": "error", "error": str(exc), "fields_checked": len(uncertain)}

    return kb
