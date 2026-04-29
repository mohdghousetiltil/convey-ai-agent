"""Document relevance validator.

Checks whether an uploaded document is appropriate for a Victorian conveyancing
matter before running full extraction. Returns a relevance verdict so the UI
can warn the user about clearly wrong or suspicious documents.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

LOG = logging.getLogger(__name__)

# Keyword signals strongly indicating a conveyancing document
_STRONG_SIGNALS = [
    r"section\s*32", r"vendor.{0,10}statement", r"title\s*search",
    r"volume\s*\d{4,}", r"folio\s*\d+", r"land\s+tax", r"rates\s+certificate",
    r"water\s+authority", r"owners?\s+corporation", r"planning\s+certificate",
    r"building\s+permit", r"occupancy\s+permit", r"conveyancing",
    r"transfer\s+of\s+land", r"property\s+address", r"registered\s+proprietor",
    r"plan\s+of\s+subdivision", r"lot\s+\d+\s+on\s+plan",
    r"council\s+rates", r"bushfire", r"vendor.{0,6}form",
]

# Signals that suggest a wrong/unrelated document
_WRONG_DOC_SIGNALS = [
    r"medicare", r"tax\s+return", r"income\s+tax", r"passport",
    r"driver.{0,6}licen[cs]e", r"birth\s+certificate", r"marriage\s+certificate",
    r"medical\s+record", r"hospital", r"prescription", r"recipe",
    r"invoice", r"bank\s+statement", r"insurance\s+claim(?!\s+schedule)",
    r"employment\s+contract", r"resume\|curriculum\s+vitae",
]

_MAX_VALIDATE_CHARS = 3000


def validate_document_quick(filename: str, text: str) -> dict[str, Any]:
    """Fast keyword-based validation — no AI call, runs synchronously.

    Returns:
        {
            is_valid: bool,
            is_suspicious: bool,
            confidence: float (0-1),
            reason: str,
            document_type_guess: str,
        }
    """
    sample = text[:_MAX_VALIDATE_CHARS].lower()
    fname = filename.lower()

    strong_hits = sum(1 for p in _STRONG_SIGNALS if re.search(p, sample, re.I))
    wrong_hits = sum(1 for p in _WRONG_DOC_SIGNALS if re.search(p, sample, re.I))

    # Filename heuristics
    conv_filename = any(kw in fname for kw in [
        "section32", "s32", "title", "rates", "council", "water", "permit",
        "planning", "vendor", "transfer", "subdivision", "certificate", "owners",
        "oc", "land_tax", "convey", "contract"
    ])

    # Score
    score = (strong_hits * 2 + int(conv_filename)) / (len(_STRONG_SIGNALS) * 2 + 1)
    score = min(1.0, score)

    is_suspicious = wrong_hits >= 2 and strong_hits == 0
    is_valid = strong_hits >= 1 or conv_filename

    if is_suspicious:
        reason = f"Document contains {wrong_hits} non-conveyancing signals and no conveyancing signals."
        doc_type = "unknown"
    elif is_valid:
        doc_type = _guess_type_from_signals(sample, fname)
        reason = f"Document matches {strong_hits} conveyancing signal(s)."
    else:
        # Ambiguous — let through but flag low confidence
        doc_type = _guess_type_from_signals(sample, fname)
        reason = "Document does not clearly match known conveyancing document types."

    return {
        "is_valid": is_valid or (not is_suspicious),  # default allow unless clearly wrong
        "is_suspicious": is_suspicious or (strong_hits == 0 and not conv_filename),
        "confidence": round(score, 2),
        "reason": reason,
        "document_type_guess": doc_type,
    }


def validate_document_ai(
    filename: str,
    text: str,
    ai_client: Any,
    provider: str,
    model: str,
) -> dict[str, Any]:
    """AI-powered validation — more accurate but slower.

    Calls the AI to classify the document type and assess relevance.
    """
    sample = text[:_MAX_VALIDATE_CHARS]
    prompt = f"""You are a Victorian conveyancing expert assistant.
Determine if the following document is relevant to a Victorian property conveyancing matter.

Filename: {filename}

Document text (first {len(sample)} characters):
---
{sample}
---

Respond with JSON only:
{{
  "is_conveyancing_document": true/false,
  "is_suspicious": true/false,
  "confidence": 0.0-1.0,
  "document_type": "one of: vendor_form, vic_title, council_rates, water_authority, planning_certificate, building_approval, owners_corporation, land_tax, contract_of_sale, general_legal, unknown",
  "reason": "brief explanation",
  "warning_message": "if suspicious, what to tell the user — null otherwise"
}}
"""
    try:
        raw = _call_ai(ai_client, provider, model, prompt)
        data = _parse_json(raw)
        if data:
            return {
                "is_valid": bool(data.get("is_conveyancing_document", True)),
                "is_suspicious": bool(data.get("is_suspicious", False)),
                "confidence": float(data.get("confidence", 0.5)),
                "reason": str(data.get("reason", "")),
                "document_type_guess": str(data.get("document_type", "unknown")),
                "warning_message": data.get("warning_message"),
            }
    except Exception as exc:
        LOG.warning("AI document validation failed: %s", exc)
    # Fallback
    return validate_document_quick(filename, text)


def _guess_type_from_signals(text: str, filename: str) -> str:
    rules = [
        (r"section\s*32|vendor.{0,10}statement|vendor.{0,6}form", "vendor_form"),
        (r"volume\s*\d{4,}|folio|registered\s+proprietor|transfer\s+of\s+land", "vic_title"),
        (r"council\s+rates|responsible\s+authority.*council|municipal", "council_rates"),
        (r"water\s+authority|water\s+supply|south\s+east\s+water|yarra\s+valley", "water_authority"),
        (r"planning\s+certificate|zone.*overlay|planning\s+scheme", "planning_certificate"),
        (r"building\s+permit|building\s+surveyor|building\s+approval", "building_approval"),
        (r"owners?\s+corporation|body\s+corporate", "owners_corporation"),
        (r"land\s+tax|state\s+revenue\s+office|sro", "land_tax"),
        (r"contract\s+of\s+sale|purchase\s+price|special\s+conditions", "contract_of_sale"),
    ]
    for pattern, doc_type in rules:
        if re.search(pattern, text, re.I):
            return doc_type
    # Try filename
    fname_rules = [
        ("s32", "vendor_form"), ("section32", "vendor_form"), ("title", "vic_title"),
        ("council", "council_rates"), ("water", "water_authority"),
        ("planning", "planning_certificate"), ("building", "building_approval"),
        ("oc", "owners_corporation"), ("landtax", "land_tax"),
    ]
    for kw, doc_type in fname_rules:
        if kw in filename.lower():
            return doc_type
    return "general"


def _call_ai(client: Any, provider: str, model: str, prompt: str) -> str:
    if provider == "anthropic":
        msg = client.messages.create(
            model=model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text if msg.content else ""
    elif provider == "google":
        return client.generate_content(prompt).text
    else:
        resp = client.chat.completions.create(
            model=model, max_tokens=512,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a document classifier. Respond with JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    if m:
        raw = m.group(1)
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{[\s\S]+\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None
