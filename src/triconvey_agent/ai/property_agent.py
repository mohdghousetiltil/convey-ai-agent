"""Unified multi-document AI property agent (Phase 3).

Architecture
-----------
Instead of running one AI call per document, this agent:
  1. Concatenates ALL uploaded PDF texts into a single prompt
  2. Sends ONE call to GPT-4o (128K context — easily fits 15+ pages)
  3. Receives a complete property knowledge base with cross-document reasoning
  4. Every field is backed by a verbatim quote + source filename

Benefits over per-document extraction
---------------------------------------
  • AI can see ALL documents simultaneously — can detect conflicts
  • AI knows which document is authoritative for each field
  • One round-trip = faster, cheaper, more coherent
  • Self-consistency: AI can note when land tax cert says X but title says Y

Anti-hallucination
------------------
  • Every field requires: value + quote + source_file
  • Code verifies each quote is a real substring of that file's raw text
  • Unverified quotes → confidence ×0.10 (practically rejected)
  • AI explicitly told: "return null rather than guess"
"""
from __future__ import annotations

import json
import re
from typing import Any

from triconvey_agent.ai.client import AIClient
from triconvey_agent.schemas.documents import Document

# ---------------------------------------------------------------------------
# How many characters of each document to send (GPT-4o has 128K tokens)
# 16 docs × 8000 chars ≈ 128K chars ≈ 32K tokens — well within limit
# ---------------------------------------------------------------------------
_MAX_CHARS_PER_DOC = 8_000

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert Australian conveyancing paralegal in Victoria.
You have been given multiple documents related to a single property sale.
Your job is to build a complete, accurate property knowledge base for a Section 32 Vendor's Statement.

CRITICAL RULES — READ FIRST:
1. Every field you fill MUST be supported by a verbatim quote from one of the documents.
2. The "quote" field must be copy-pasteable text that appears word-for-word in the document.
3. The "source_file" field must match exactly one of the filenames listed above the document.
4. If you cannot find the information, return null — NEVER invent values.
5. When documents conflict, record the conflict. Do NOT silently pick one.
6. Services: a service listed as connected = true. A service not mentioned = false (not connected).
7. Dates: use the date PRINTED ON the document, not today's date or the search run date.
"""

_USER_PROMPT_TEMPLATE = """\
DOCUMENTS UPLOADED:
{document_blocks}

TASK — Return ONLY the JSON object below (no markdown, no explanation).
Fill every field you can find. Use null when genuinely not found.

{{
  "property": {{
    "address":           {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "lot_number":        {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "plan_number":       {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "volume_folio":      {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "council":           {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "land_description":  {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}}
  }},
  "vendor": {{
    "full_name":         {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "address":           {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "phone":             {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "email":             {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "is_trustee":        {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "trust_name":        {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}}
  }},
  "services": {{
    "mains_electricity": {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "mains_gas":         {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "mains_water":       {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "mains_sewerage":    {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "telephone_or_nbn":  {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "bottled_gas":       {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "tank_water":        {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "septic_tank":       {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}}
  }},
  "rates": {{
    "council_annual":     {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "water_annual":       {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "land_tax_annual":    {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "owners_corp_annual": {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "council_authority":  {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "water_authority":    {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}}
  }},
  "planning": {{
    "zone":                        {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "overlay":                     {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "bushfire_prone":              {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "gaic_applies":                {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "building_permits_last_7yrs":  {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}},
    "owners_corporation_exists":   {{"value": null, "quote": null, "source_file": null, "confidence": 0.0}}
  }},
  "documents": [
    {{
      "source_file": "<filename>",
      "document_category": "<category>",
      "display_name": "<human label>",
      "issuing_authority": null,
      "document_date": null,
      "quote_date": null,
      "is_section32_attachment": true,
      "key_facts": []
    }}
  ],
  "conflicts": [
    {{
      "field": "<field name>",
      "source_a": "<filename>",
      "value_a": "<value from source A>",
      "source_b": "<filename>",
      "value_b": "<value from source B>",
      "recommended": "<which value to trust and why>"
    }}
  ],
  "missing_information": [
    "<description of information that should be present for a complete Section 32 but is not in any document>"
  ]
}}

SERVICES RULES:
  • Only list a service as true if the document explicitly states it is connected.
  • "VOIP phone via NBN", "NBN", "Fibre NBN", "Internet" → telephone_or_nbn = true
  • "Mains Sewerage" or "Sewerage" → mains_sewerage = true
  • Any service not listed in the vendor form as connected → false

DOCUMENT DATE RULES:
  • document_date must be the date PRINTED ON that certificate/report.
  • Do NOT use the date a search was ordered or today's date.
  • quote_date must be the exact text in the document that contains that date.
  • If unsure, set both to null.

DOCUMENTS LIST (fill one entry per uploaded document):
  Return one object per document in the "documents" array.
  Categories: vic_title_register_search, plan_of_subdivision, plan_of_consolidation,
  vic_roads_certificate, land_tax_certificate, council_rates_certificate,
  council_building_approval_certificate, water_information_statement,
  water_encumbrance_certificate, building_permit, certificate_of_final_inspection,
  occupancy_permit, owners_corporation_certificate, domestic_building_warranty_insurance,
  vendor_instruction_form, other_government_certificate, other_legal_document, unknown.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_document_blocks(docs: list[Document]) -> str:
    """Concatenate each document's text with a filename header."""
    blocks: list[str] = []
    for doc in docs:
        if not doc.is_pdf:
            continue
        text = (doc.raw_text or doc.normalized_text or "").strip()
        if not text:
            continue
        truncated = text[:_MAX_CHARS_PER_DOC]
        if len(text) > _MAX_CHARS_PER_DOC:
            truncated += f"\n[... truncated at {_MAX_CHARS_PER_DOC} chars ...]"
        blocks.append(f"=== FILE: {doc.filename} ===\n{truncated}")
    return "\n\n".join(blocks)


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
        raise ValueError(f"No valid JSON in AI response: {text[:300]}")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _text_map(docs: list[Document]) -> dict[str, str]:
    """Map filename → raw_text for quote verification."""
    return {
        doc.filename: (doc.raw_text or doc.normalized_text or "")
        for doc in docs
        if doc.is_pdf
    }


def _verify_field(field: dict[str, Any], texts: dict[str, str]) -> dict[str, Any]:
    """Check that the quote for a single field object actually exists in the source file."""
    if not isinstance(field, dict):
        return field
    if field.get("value") is None:
        return field

    quote = field.get("quote")
    source = field.get("source_file")
    if not quote or not source:
        field["quote_verified"] = False
        field["confidence"] = float(field.get("confidence", 0.0)) * 0.40
        return field

    raw = texts.get(source, "")
    # Exact match
    if quote in raw:
        field["quote_verified"] = True
        return field
    # Whitespace-normalised match
    if " ".join(quote.split()) in " ".join(raw.split()):
        field["quote_verified"] = True
        return field

    # Quote not found → penalty
    field["quote_verified"] = False
    field["confidence"] = float(field.get("confidence", 0.0)) * 0.10
    return field


def _verify_document_dates(docs_list: list[dict[str, Any]], texts: dict[str, str]) -> list[dict[str, Any]]:
    """Verify quote_date for each document summary, null document_date if unverifiable."""
    verified: list[dict[str, Any]] = []
    for doc in docs_list:
        quote = doc.get("quote_date")
        source = doc.get("source_file", "")
        raw = texts.get(source, "")
        if quote and raw:
            if quote in raw or " ".join(quote.split()) in " ".join(raw.split()):
                doc["date_verified"] = True
            else:
                doc["date_verified"] = False
                doc["document_date"] = None
        else:
            doc["date_verified"] = bool(not quote and not doc.get("document_date"))
            if not quote:
                doc["document_date"] = None
        verified.append(doc)
    return verified


def _verify_knowledge_base(kb: dict[str, Any], texts: dict[str, str]) -> dict[str, Any]:
    """Walk all field objects in the knowledge base and verify quotes."""
    for section_key in ("property", "vendor", "services", "rates", "planning"):
        section = kb.get(section_key, {})
        for field_key, field_val in section.items():
            if isinstance(field_val, dict) and "value" in field_val:
                section[field_key] = _verify_field(field_val, texts)

    docs_list = kb.get("documents", [])
    if isinstance(docs_list, list):
        kb["documents"] = _verify_document_dates(docs_list, texts)

    return kb


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class PropertyKnowledgeBase:
    """Typed wrapper around the raw knowledge base dict."""

    def __init__(self, raw: dict[str, Any], source_files: list[str]):
        self.raw = raw
        self.source_files = source_files

    # --- convenience getters ---

    def _field(self, section: str, key: str) -> dict[str, Any] | None:
        return self.raw.get(section, {}).get(key)

    def get_value(self, section: str, key: str) -> Any:
        f = self._field(section, key)
        return f.get("value") if f else None

    def get_confidence(self, section: str, key: str) -> float:
        f = self._field(section, key)
        return float(f.get("confidence", 0.0)) if f else 0.0

    def get_verified(self, section: str, key: str) -> bool:
        f = self._field(section, key)
        return bool(f.get("quote_verified", False)) if f else False

    # --- structured accessors for pipeline use ---

    @property
    def documents(self) -> list[dict[str, Any]]:
        return self.raw.get("documents", [])

    @property
    def conflicts(self) -> list[dict[str, Any]]:
        return self.raw.get("conflicts", [])

    @property
    def missing_information(self) -> list[str]:
        return self.raw.get("missing_information", [])

    def as_source_documents(self) -> list[dict[str, Any]]:
        """Convert to the source_documents format consumed by normalized_builder."""
        results: list[dict[str, Any]] = []
        for doc in self.documents:
            results.append({
                "source_file": doc.get("source_file", ""),
                "document_category": doc.get("document_category", "unknown"),
                "display_name": doc.get("display_name") or doc.get("source_file", ""),
                "issuing_authority": doc.get("issuing_authority"),
                "document_date": doc.get("document_date"),
                "quote_date": doc.get("quote_date"),
                "date_verified": doc.get("date_verified", False),
                "property_address": doc.get("property_address"),
                "key_facts": doc.get("key_facts") or [],
                "is_section32_attachment": bool(doc.get("is_section32_attachment", False)),
                "ai_error": None,
            })
        return results

    def as_agent_summary(self) -> dict[str, Any]:
        """Build a summary dict for the review_summary / audit trail."""
        fields_total = 0
        fields_filled = 0
        fields_verified = 0

        for section in ("property", "vendor", "services", "rates", "planning"):
            for _, field in self.raw.get(section, {}).items():
                if not isinstance(field, dict):
                    continue
                fields_total += 1
                if field.get("value") is not None:
                    fields_filled += 1
                if field.get("quote_verified"):
                    fields_verified += 1

        return {
            "mode": "unified_agent",
            "source_files_processed": len(self.source_files),
            "fields_total": fields_total,
            "fields_filled": fields_filled,
            "fields_verified": fields_verified,
            "conflicts_detected": len(self.conflicts),
            "missing_information_items": len(self.missing_information),
        }


def run_property_agent(docs: list[Document], client: AIClient) -> PropertyKnowledgeBase:
    """Run the unified multi-document AI agent.

    Sends ALL uploaded PDFs in a single GPT-4o call and returns a structured
    PropertyKnowledgeBase with verified quotes for every extracted field.

    Args:
        docs:   All Document objects loaded by the pipeline (PDF + YAML).
        client: AIClient wrapping GPT-4o (or any compatible model).

    Returns:
        PropertyKnowledgeBase — a typed wrapper exposing convenience getters.
    """
    pdf_docs = [d for d in docs if d.is_pdf]
    source_files = [d.filename for d in pdf_docs]
    texts = _text_map(pdf_docs)

    document_blocks = _build_document_blocks(pdf_docs)
    if not document_blocks.strip():
        return PropertyKnowledgeBase(raw={}, source_files=source_files)

    user_prompt = _USER_PROMPT_TEMPLATE.format(document_blocks=document_blocks)
    full_prompt = f"{_SYSTEM_PROMPT}\n\n{user_prompt}"

    try:
        response = client.complete(full_prompt)
        raw = _parse_json(response.raw_text)
    except Exception as exc:  # noqa: BLE001
        # Return an empty KB with the error recorded
        return PropertyKnowledgeBase(
            raw={"agent_error": str(exc), "documents": [], "conflicts": [], "missing_information": []},
            source_files=source_files,
        )

    # Verify all quotes against source text
    raw = _verify_knowledge_base(raw, texts)

    # Ensure every uploaded PDF appears in the documents list even if AI missed it
    covered = {d.get("source_file", "") for d in raw.get("documents", [])}
    for filename in source_files:
        if filename not in covered:
            raw.setdefault("documents", []).append({
                "source_file": filename,
                "document_category": "unknown",
                "display_name": filename,
                "issuing_authority": None,
                "document_date": None,
                "quote_date": None,
                "date_verified": False,
                "is_section32_attachment": False,
                "key_facts": [],
            })

    return PropertyKnowledgeBase(raw=raw, source_files=source_files)
