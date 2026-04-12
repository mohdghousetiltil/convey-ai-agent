"""Grounded AI extraction for each supported document type.

Anti-hallucination design
--------------------------
Every field the AI returns must include a verbatim "quote" copied directly
from the document text.  After the AI responds, verify_quotes() checks that
each quote is an actual substring of the raw document text.

  quote present + verified   → full confidence (e.g. 0.92)
  quote present + unverified → confidence × 0.10  (rejected as likely hallucinated)
  quote absent               → confidence × 0.40  (AI guessed without citing)

Supported document types
------------------------
  vendor_form     → AIVendorFormExtract
  vic_title       → AITitleExtract
  property_report → AIRatesCertExtract  (authority name, amounts, dates)
  planning_report → (planning fields folded into vendor form reconciliation)
"""
from __future__ import annotations

import json
import re
from typing import Any

from triconvey_agent.ai.client import AIClient
from triconvey_agent.schemas.ai_extract import (
    AIDocumentExtract,
    AIRatesCertExtract,
    AIServicesExtract,
    AITitleExtract,
    AIVendorFormExtract,
    GroundedValue,
)
from triconvey_agent.schemas.documents import Document, DocumentType

# Maximum characters of document text sent to AI.
# GPT-4o has 128k context; vendor forms are typically 4-8k chars.
_MAX_TEXT_CHARS = 14_000


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_VENDOR_FORM_PROMPT = """\
You are extracting structured data from a Victorian vendor instruction form \
(Australian property conveyancing).

SOURCE DOCUMENT TEXT (verbatim):
---
{text}
---

TASK
Extract every field listed below from the document text.
For each field provide three keys:
  "value"      — the extracted value (true/false for booleans, string for text, null if not present)
  "quote"      — the EXACT verbatim substring from the document above that proves your answer
                 (must be copy-pasteable; null only when value is null)
  "confidence" — float 0.0–1.0 (how clearly the text supports this answer)

ANTI-HALLUCINATION RULES (strictly enforced):
1. NEVER invent a value. If the field is not in the document, return null.
2. "quote" MUST be a verbatim copy of text that appears word-for-word above.
3. Reduce confidence when the evidence is indirect or ambiguous.
4. Prefer null + low confidence over a hallucinated value + high confidence.

SERVICES SECTION RULES:
The document has a section headed "Are the services listed connected to the land?"
listing services that are ticked (connected). Anything NOT in the list is NOT connected.
  • "VOIP phone via NBN", "NBN", "Fibre NBN", "Internet"  → telephone_or_nbn = true
  • "Mains Sewerage", "Sewerage"                           → mains_sewerage = true
  • "Mains Gas"                                            → mains_gas = true (not bottled_gas)
  • "Bottled Gas", "LPG"                                   → bottled_gas = true (not mains_gas)
  • "Mains Water"                                          → mains_water = true (not tank_water)
  • "Tank Water", "Water Tank"                             → tank_water = true (not mains_water)
  • "Septic Tank", "Septic"                                → septic_tank = true
  • Service NOT appearing in the list                      → that service = false

BOOLEAN QUESTION RULES:
  "Yes" answer → true, "No" answer → false, not found → null.

Return ONLY this JSON (no markdown fences, no explanation):
{{
  "services": {{
    "mains_electricity": {{"value": null, "quote": null, "confidence": 0.0}},
    "mains_gas":         {{"value": null, "quote": null, "confidence": 0.0}},
    "mains_water":       {{"value": null, "quote": null, "confidence": 0.0}},
    "mains_sewerage":    {{"value": null, "quote": null, "confidence": 0.0}},
    "telephone_or_nbn":  {{"value": null, "quote": null, "confidence": 0.0}},
    "bottled_gas":       {{"value": null, "quote": null, "confidence": 0.0}},
    "tank_water":        {{"value": null, "quote": null, "confidence": 0.0}},
    "septic_tank":       {{"value": null, "quote": null, "confidence": 0.0}}
  }},
  "council":                      {{"value": null, "quote": null, "confidence": 0.0}},
  "water_authority":               {{"value": null, "quote": null, "confidence": 0.0}},
  "council_rates":                 {{"value": null, "quote": null, "confidence": 0.0}},
  "water_rates":                   {{"value": null, "quote": null, "confidence": 0.0}},
  "owners_corporation":            {{"value": null, "quote": null, "confidence": 0.0}},
  "insured":                       {{"value": null, "quote": null, "confidence": 0.0}},
  "bushfire_prone_area":           {{"value": null, "quote": null, "confidence": 0.0}},
  "gaic_trigger":                  {{"value": null, "quote": null, "confidence": 0.0}},
  "building_permits_last_7_years": {{"value": null, "quote": null, "confidence": 0.0}},
  "improved_past_6_5_years":       {{"value": null, "quote": null, "confidence": 0.0}}
}}"""

_VIC_TITLE_PROMPT = """\
You are extracting structured data from a Victorian Certificate of Title \
(Register Search Statement).

SOURCE DOCUMENT TEXT (verbatim):
---
{text}
---

TASK
Extract every field listed below. For each field:
  "value"      — extracted value (string), null if not found
  "quote"      — EXACT verbatim substring from the document that proves your answer
  "confidence" — float 0.0–1.0

ANTI-HALLUCINATION RULES (strictly enforced):
1. Return null if the field is not in the document.
2. "quote" must be a verbatim substring copy-pasted from above.
3. volume_folio format: "VOLUME XXXXX FOLIO XXX" (uppercase).
4. plan_type: one of "Plan of Subdivision", "Plan of Consolidation", "Lodged Plan", "Title Plan".
5. plan_number: e.g. "PS826454D", "LP123456", "PC12345".

Return ONLY this JSON (no markdown fences, no explanation):
{{
  "volume_folio":          {{"value": null, "quote": null, "confidence": 0.0}},
  "plan_number":           {{"value": null, "quote": null, "confidence": 0.0}},
  "plan_type":             {{"value": null, "quote": null, "confidence": 0.0}},
  "registered_proprietor": {{"value": null, "quote": null, "confidence": 0.0}},
  "lot_description":       {{"value": null, "quote": null, "confidence": 0.0}}
}}"""

_RATES_CERT_PROMPT = """\
You are extracting structured data from a Victorian rates, water, or \
authority certificate / enquiry document.

SOURCE DOCUMENT TEXT (verbatim):
---
{text}
---

TASK
Extract every field listed below. For each field:
  "value"      — extracted value (string), null if not found
  "quote"      — EXACT verbatim substring from the document that proves your answer
  "confidence" — float 0.0–1.0

ANTI-HALLUCINATION RULES:
1. Return null if not in the document.
2. "quote" must be verbatim from the text above.
3. annual_amount: the total annual charge (include currency symbol if present, e.g. "$1,234.56").
4. certificate_date: date as it appears in the document (dd/mm/yyyy preferred).

Return ONLY this JSON (no markdown fences, no explanation):
{{
  "authority_name":   {{"value": null, "quote": null, "confidence": 0.0}},
  "property_address": {{"value": null, "quote": null, "confidence": 0.0}},
  "annual_amount":    {{"value": null, "quote": null, "confidence": 0.0}},
  "certificate_date": {{"value": null, "quote": null, "confidence": 0.0}}
}}"""


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

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
            return json.loads(match.group(0))
        raise ValueError(f"No JSON found in AI response: {text[:200]}")


def _gv(raw: Any) -> GroundedValue:
    """Safely parse a raw dict from AI response into a GroundedValue."""
    if not isinstance(raw, dict):
        return GroundedValue()
    return GroundedValue(
        value=raw.get("value"),
        quote=raw.get("quote") or None,
        confidence=float(raw.get("confidence") or 0.0),
    )


# ---------------------------------------------------------------------------
# Quote verification
# ---------------------------------------------------------------------------

def _verify_quotes(extract: AIDocumentExtract, raw_text: str) -> AIDocumentExtract:
    """Check every GroundedValue.quote against the document's raw text.

    Sets quote_verified=True when the quote is a genuine substring.
    Increments fields_total, fields_verified, fields_rejected on the extract.
    """
    total = verified = rejected = 0

    def check(gv: GroundedValue) -> GroundedValue:
        nonlocal total, verified, rejected
        if gv.value is None:
            return gv
        total += 1
        if gv.quote and gv.quote in raw_text:
            verified += 1
            return gv.model_copy(update={"quote_verified": True})
        if gv.quote:
            # Quote supplied but not found verbatim — try a looser check
            # (handles minor whitespace differences from PDF extraction)
            normalised_quote = " ".join(gv.quote.split())
            normalised_text  = " ".join(raw_text.split())
            if normalised_quote in normalised_text:
                verified += 1
                return gv.model_copy(update={"quote_verified": True})
            rejected += 1
        return gv

    def walk(obj: Any) -> Any:
        if isinstance(obj, GroundedValue):
            return check(obj)
        if isinstance(obj, BaseModel):
            updates = {}
            for field_name in obj.model_fields:
                child = getattr(obj, field_name)
                walked = walk(child)
                if walked is not child:
                    updates[field_name] = walked
            return obj.model_copy(update=updates) if updates else obj
        return obj

    # We need BaseModel in scope for walk()
    from pydantic import BaseModel  # noqa: PLC0415

    if extract.vendor_form is not None:
        extract = extract.model_copy(update={"vendor_form": walk(extract.vendor_form)})
    if extract.vic_title is not None:
        extract = extract.model_copy(update={"vic_title": walk(extract.vic_title)})
    if extract.rates_cert is not None:
        extract = extract.model_copy(update={"rates_cert": walk(extract.rates_cert)})

    return extract.model_copy(update={
        "fields_total": total,
        "fields_verified": verified,
        "fields_rejected": rejected,
    })


# ---------------------------------------------------------------------------
# Per-document-type extractors
# ---------------------------------------------------------------------------

def _truncate(text: str) -> str:
    return text[:_MAX_TEXT_CHARS]


def _extract_vendor_form(doc: Document, client: AIClient) -> AIDocumentExtract:
    text = _truncate(doc.raw_text or doc.normalized_text)
    prompt = _VENDOR_FORM_PROMPT.format(text=text)
    response = client.complete(prompt)
    raw = _parse_json(response.raw_text)

    svc_raw = raw.get("services", {})
    vendor = AIVendorFormExtract(
        services=AIServicesExtract(
            mains_electricity=_gv(svc_raw.get("mains_electricity")),
            mains_gas=        _gv(svc_raw.get("mains_gas")),
            mains_water=      _gv(svc_raw.get("mains_water")),
            mains_sewerage=   _gv(svc_raw.get("mains_sewerage")),
            telephone_or_nbn= _gv(svc_raw.get("telephone_or_nbn")),
            bottled_gas=      _gv(svc_raw.get("bottled_gas")),
            tank_water=       _gv(svc_raw.get("tank_water")),
            septic_tank=      _gv(svc_raw.get("septic_tank")),
        ),
        council=                      _gv(raw.get("council")),
        water_authority=              _gv(raw.get("water_authority")),
        council_rates=                _gv(raw.get("council_rates")),
        water_rates=                  _gv(raw.get("water_rates")),
        owners_corporation=           _gv(raw.get("owners_corporation")),
        insured=                      _gv(raw.get("insured")),
        bushfire_prone_area=          _gv(raw.get("bushfire_prone_area")),
        gaic_trigger=                 _gv(raw.get("gaic_trigger")),
        building_permits_last_7_years=_gv(raw.get("building_permits_last_7_years")),
        improved_past_6_5_years=      _gv(raw.get("improved_past_6_5_years")),
    )
    result = AIDocumentExtract(
        source_file=doc.filename,
        document_type=doc.document_type.value,
        vendor_form=vendor,
    )
    return _verify_quotes(result, doc.raw_text or doc.normalized_text)


def _extract_vic_title(doc: Document, client: AIClient) -> AIDocumentExtract:
    text = _truncate(doc.raw_text or doc.normalized_text)
    prompt = _VIC_TITLE_PROMPT.format(text=text)
    response = client.complete(prompt)
    raw = _parse_json(response.raw_text)

    title = AITitleExtract(
        volume_folio=          _gv(raw.get("volume_folio")),
        plan_number=           _gv(raw.get("plan_number")),
        plan_type=             _gv(raw.get("plan_type")),
        registered_proprietor= _gv(raw.get("registered_proprietor")),
        lot_description=       _gv(raw.get("lot_description")),
    )
    result = AIDocumentExtract(
        source_file=doc.filename,
        document_type=doc.document_type.value,
        vic_title=title,
    )
    return _verify_quotes(result, doc.raw_text or doc.normalized_text)


def _extract_rates_cert(doc: Document, client: AIClient) -> AIDocumentExtract:
    text = _truncate(doc.raw_text or doc.normalized_text)
    prompt = _RATES_CERT_PROMPT.format(text=text)
    response = client.complete(prompt)
    raw = _parse_json(response.raw_text)

    cert = AIRatesCertExtract(
        authority_name=   _gv(raw.get("authority_name")),
        property_address= _gv(raw.get("property_address")),
        annual_amount=    _gv(raw.get("annual_amount")),
        certificate_date= _gv(raw.get("certificate_date")),
    )
    result = AIDocumentExtract(
        source_file=doc.filename,
        document_type=doc.document_type.value,
        rates_cert=cert,
    )
    return _verify_quotes(result, doc.raw_text or doc.normalized_text)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_PROPERTY_REPORT_TYPES = {
    DocumentType.PROPERTY_REPORT,
    DocumentType.PLANNING_REPORT,
}


def extract_document_with_ai(doc: Document, client: AIClient) -> AIDocumentExtract | None:
    """Run grounded AI extraction for a single document.

    Returns None for document types that don't have an AI extractor yet.
    Any exception is caught and returned as extraction_error so the caller
    can fall back to rules-only output without crashing.
    """
    try:
        if doc.document_type == DocumentType.VENDOR_FORM:
            return _extract_vendor_form(doc, client)
        if doc.document_type == DocumentType.VIC_TITLE:
            return _extract_vic_title(doc, client)
        if doc.document_type in _PROPERTY_REPORT_TYPES:
            return _extract_rates_cert(doc, client)
    except Exception as exc:  # noqa: BLE001
        return AIDocumentExtract(
            source_file=doc.filename,
            document_type=doc.document_type.value,
            extraction_error=str(exc),
        )
    return None


def extract_all_documents_with_ai(
    docs: list[Document],
    client: AIClient,
) -> list[AIDocumentExtract]:
    """Run AI extraction on every supported document in the list."""
    results: list[AIDocumentExtract] = []
    for doc in docs:
        result = extract_document_with_ai(doc, client)
        if result is not None:
            results.append(result)
    return results
