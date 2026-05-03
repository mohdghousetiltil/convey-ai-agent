"""Document type router — classifies a document before extractors run.

Pipeline
--------
1. Rule-based fast classifier (filename + first 2 pages of text).
2. AI fallback — only called when rules produce no confident match.

Returns a ``DocRouteResult`` that ``fact_extraction.py`` uses to select
the correct extractor, stopping all other extractors from touching the file.

Design rules
------------
- Rules handle >95% of documents without an AI call.
- AI classification is cheap (short prompt, single JSON response).
- Unknown docs fall back to running all extractors so nothing is missed.
- Every routing decision is logged to ``doc_router_debug.json``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from triconvey_agent.schemas.documents import Document
    from triconvey_agent.ai.client import AIClient

_log = logging.getLogger(__name__)

_DEBUG_LOG_PATH = Path.cwd() / "doc_router_debug.json"

# ---------------------------------------------------------------------------
# Public doc-type constants
# ---------------------------------------------------------------------------

DOC_TYPE_COUNCIL = "council_rates"
DOC_TYPE_WATER = "water_authority"
DOC_TYPE_LAND_TAX = "land_tax"
DOC_TYPE_OC = "owners_corporation"
DOC_TYPE_VENDOR = "vendor_form"
DOC_TYPE_PLANNING = "planning"
DOC_TYPE_BUILDING = "building"
DOC_TYPE_TITLE = "title"
DOC_TYPE_UNKNOWN = "unknown"

ALL_DOC_TYPES = frozenset({
    DOC_TYPE_COUNCIL, DOC_TYPE_WATER, DOC_TYPE_LAND_TAX, DOC_TYPE_OC,
    DOC_TYPE_VENDOR, DOC_TYPE_PLANNING, DOC_TYPE_BUILDING, DOC_TYPE_TITLE,
    DOC_TYPE_UNKNOWN,
})

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class DocRouteResult:
    doc_type: str
    confidence: float
    evidence: str
    method: str  # "rule" | "ai" | "fallback"


# ---------------------------------------------------------------------------
# Rule patterns — ordered from most specific to most generic within each type.
# All patterns are checked against (filename + first-2-pages text).
# ---------------------------------------------------------------------------

_RULE_PATTERNS: dict[str, list[tuple[re.Pattern, float, str]]] = {
    # (pattern, confidence, evidence_label)
    DOC_TYPE_COUNCIL: [
        (re.compile(r"land\s+information\s+certificate", re.I), 0.99, "Land Information Certificate"),
        (re.compile(r"section\s+121\s+of\s+the\s+local\s+government", re.I), 0.97, "Section 121 LGA"),
        (re.compile(r"council\s+rates?\s+(?:notice|assessment|statement|certificate)", re.I), 0.95, "Council Rates Notice"),
        (re.compile(r"municipal\s+rates?\s+(?:assessment|notice|certificate)", re.I), 0.95, "Municipal Rates"),
        (re.compile(r"annual\s+rates?\s+and\s+charges", re.I), 0.90, "Annual Rates and Charges"),
        (re.compile(r"local\s+government\s+(?:act|rates?\s+notice)", re.I), 0.88, "Local Government Act"),
        (re.compile(r"rates?\s+(?:notice|assessment|certificate)\s*\n", re.I), 0.86, "Rates Notice title"),
    ],
    DOC_TYPE_WATER: [
        (re.compile(r"water\s+information\s+statement", re.I), 0.99, "Water Information Statement"),
        (re.compile(r"section\s+158\s+of\s+the\s+water\s+act", re.I), 0.99, "Section 158 Water Act"),
        (re.compile(r"section\s+158\s+statement", re.I), 0.97, "Section 158 Statement"),
        (re.compile(r"statement\s+under\s+section\s+158", re.I), 0.97, "Statement under Section 158"),
        (re.compile(r"(?:information\s+statement|rate\s+certificate)\s+no\.?:?\s*\d{6,}", re.I), 0.93, "Cert No pattern"),
        (re.compile(r"water\s+act\s+1989", re.I), 0.88, "Water Act 1989"),
    ],
    DOC_TYPE_LAND_TAX: [
        (re.compile(r"land\s+tax\s+certificate", re.I), 0.99, "Land Tax Certificate"),
        (re.compile(r"state\s+revenue\s+office", re.I), 0.97, "State Revenue Office"),
        (re.compile(r"land\s+tax\s+assessment", re.I), 0.95, "Land Tax Assessment"),
        (re.compile(r"land\s+tax\s+act\s+2005", re.I), 0.95, "Land Tax Act 2005"),
    ],
    DOC_TYPE_OC: [
        (re.compile(r"owners?\s+corporation\s+(?:search\s+)?(?:report|certificate|basic\s+report|information)", re.I), 0.99, "OC Certificate/Report"),
        (re.compile(r"owners?\s+corporation\s+levy\s+notice", re.I), 0.99, "OC Levy Notice"),
        (re.compile(r"body\s+corporate\s+(?:certificate|search\s+report|levy)", re.I), 0.97, "Body Corporate cert"),
        (re.compile(r"strata\s+(?:certificate|fees?\s+schedule|levy\s+notice)", re.I), 0.95, "Strata certificate"),
        (re.compile(r"oc\s+(?:certificate|report|fee\s+schedule|levy\s+notice|search)", re.I), 0.92, "OC report/cert"),
    ],
    DOC_TYPE_VENDOR: [
        (re.compile(r"vendor(?:'?s?)?\s+(?:statement|disclosure|declaration)", re.I), 0.98, "Vendor Statement"),
        (re.compile(r"section\s+32\s+(?:statement|vendor|disclosure)", re.I), 0.98, "Section 32"),
        (re.compile(r"(?:s32|sect\.?\s*32)\s+(?:statement|disclosure)", re.I), 0.95, "S32 reference"),
    ],
    DOC_TYPE_PLANNING: [
        (re.compile(r"planning\s+certificate", re.I), 0.98, "Planning Certificate"),
        (re.compile(r"section\s+187\s+(?:certificate|planning)", re.I), 0.98, "Section 187"),
        (re.compile(r"certificate\s+of\s+(?:compliance|planning)", re.I), 0.90, "Certificate of Compliance"),
    ],
    DOC_TYPE_BUILDING: [
        (re.compile(r"building\s+(?:approval|permit|certificate)", re.I), 0.97, "Building Approval/Permit"),
        (re.compile(r"occupancy\s+certificate", re.I), 0.97, "Occupancy Certificate"),
        (re.compile(r"building\s+commission", re.I), 0.88, "Building Commission"),
    ],
    DOC_TYPE_TITLE: [
        (re.compile(r"certificate\s+of\s+title", re.I), 0.99, "Certificate of Title"),
        (re.compile(r"folio\s+identifier", re.I), 0.97, "Folio Identifier"),
        (re.compile(r"torrens\s+title", re.I), 0.95, "Torrens Title"),
        (re.compile(r"transfer\s+of\s+land\s+act", re.I), 0.90, "Transfer of Land Act"),
    ],
}

# ---------------------------------------------------------------------------
# AI classification prompt
# ---------------------------------------------------------------------------

_AI_ROUTER_PROMPT = """\
You are classifying an Australian conveyancing document.

Based on the filename and first page text, identify the single best document type.

Document types:
- council_rates: Land Information Certificate, council rates notice, municipal rates assessment
- water_authority: Water Information Statement, section 158 statement, water authority rates
- land_tax: Land Tax Certificate, State Revenue Office land tax assessment
- owners_corporation: Owners Corporation certificate/report, body corporate certificate, strata fees schedule
- vendor_form: Vendor Statement (section 32), vendor disclosure
- planning: Planning Certificate (section 187)
- building: Building approval, building permit, occupancy certificate
- title: Certificate of Title, folio identifier, Torrens title
- unknown: Cannot determine from the supplied text

Return ONLY valid JSON with this exact shape:
{
  "document_type": "council_rates",
  "confidence": 0.98,
  "evidence": "LAND INFORMATION CERTIFICATE; Alpine Shire Council"
}

Rules:
- Use only evidence visible in the supplied filename and text.
- confidence must be a float between 0.0 and 1.0.
- evidence must be a short verbatim quote from the text (max 120 chars).
- If the text matches multiple types, pick the one with the most specific match.
- Do not invent facts.
"""


# ---------------------------------------------------------------------------
# Rule-based classifier
# ---------------------------------------------------------------------------


def _build_haystack(doc: "Document") -> str:
    """Filename + first 2 pages of text (or full text if ≤2 pages)."""
    filename = doc.filename or ""
    text_parts = [filename]

    if doc.pages:
        for page in doc.pages[:2]:
            text_parts.append(page.text or page.normalized_text or "")
    else:
        raw = doc.raw_text or doc.normalized_text or ""
        # Take first ~4000 chars as a proxy for 2 pages
        text_parts.append(raw[:4_000])

    return "\n".join(text_parts)


def _classify_by_rules(haystack: str) -> DocRouteResult | None:
    """Return a confident rule match, or None if ambiguous/no match."""
    matches: list[tuple[str, float, str]] = []

    for doc_type, patterns in _RULE_PATTERNS.items():
        for pattern, confidence, evidence_label in patterns:
            m = pattern.search(haystack)
            if m:
                matched_text = m.group(0)[:80]
                matches.append((doc_type, confidence, f"{evidence_label}: '{matched_text}'"))
                break  # first matching pattern for this doc_type wins

    if not matches:
        return None

    # Sort by confidence descending
    matches.sort(key=lambda x: -x[1])
    best_type, best_conf, best_evidence = matches[0]

    # Ambiguous: two types with close confidence → let AI decide
    if len(matches) >= 2:
        second_conf = matches[1][1]
        if best_conf - second_conf < 0.10:
            return None  # too close — escalate to AI

    return DocRouteResult(
        doc_type=best_type,
        confidence=best_conf,
        evidence=best_evidence,
        method="rule",
    )


# ---------------------------------------------------------------------------
# AI fallback classifier
# ---------------------------------------------------------------------------


def _classify_with_ai(
    haystack: str,
    ai_client: "AIClient",
) -> DocRouteResult:
    """Call the AI to classify the document. Never raises — returns unknown on failure."""
    prompt = _AI_ROUTER_PROMPT + "\n\nDOCUMENT (filename + first page):\n" + haystack[:3_000]
    try:
        result = ai_client.complete(prompt)
        raw = (result.raw_text if hasattr(result, "raw_text") else str(result)).strip()
    except Exception as exc:
        _log.warning("doc_router: AI call failed — %s", exc)
        return DocRouteResult(doc_type=DOC_TYPE_UNKNOWN, confidence=0.0, evidence="AI call failed", method="fallback")

    # Parse JSON
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return DocRouteResult(doc_type=DOC_TYPE_UNKNOWN, confidence=0.0, evidence="AI returned no JSON", method="fallback")

    try:
        obj = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return DocRouteResult(doc_type=DOC_TYPE_UNKNOWN, confidence=0.0, evidence="AI JSON parse error", method="fallback")

    doc_type = str(obj.get("document_type", "unknown")).strip().lower()
    if doc_type not in ALL_DOC_TYPES:
        doc_type = DOC_TYPE_UNKNOWN

    try:
        confidence = float(obj.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    evidence = str(obj.get("evidence", "")).strip()[:200]

    return DocRouteResult(doc_type=doc_type, confidence=confidence, evidence=evidence, method="ai")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def classify_document(
    doc: "Document",
    *,
    ai_client: "AIClient | None" = None,
) -> DocRouteResult:
    """Classify a document into one of the known conveyancing doc types.

    1. Try rule-based classification (fast, no AI call).
    2. If rules are ambiguous and ai_client is provided, ask the AI.
    3. If AI is unavailable, return unknown (callers run all extractors as fallback).
    """
    haystack = _build_haystack(doc)
    rule_result = _classify_by_rules(haystack)

    if rule_result is not None:
        _write_debug_log(doc.filename or "", rule_result, ai_called=False)
        return rule_result

    # Rules were ambiguous — try AI
    if ai_client is not None:
        ai_result = _classify_with_ai(haystack, ai_client)
        _write_debug_log(doc.filename or "", ai_result, ai_called=True)
        return ai_result

    # No AI available — unknown
    fallback = DocRouteResult(
        doc_type=DOC_TYPE_UNKNOWN,
        confidence=0.0,
        evidence="no rule match, no AI client",
        method="fallback",
    )
    _write_debug_log(doc.filename or "", fallback, ai_called=False)
    return fallback


# ---------------------------------------------------------------------------
# Debug logging
# ---------------------------------------------------------------------------


def _write_debug_log(filename: str, result: DocRouteResult, *, ai_called: bool) -> None:
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "file": filename,
        "doc_type": result.doc_type,
        "confidence": result.confidence,
        "evidence": result.evidence,
        "method": result.method,
        "ai_called": ai_called,
    }

    existing: list[dict] = []
    if _DEBUG_LOG_PATH.exists():
        try:
            existing = json.loads(_DEBUG_LOG_PATH.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    existing.append(entry)
    try:
        _DEBUG_LOG_PATH.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        _log.warning("doc_router: could not write debug log — %s", exc)
