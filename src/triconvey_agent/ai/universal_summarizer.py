"""Universal AI document summariser.

Runs GPT-4o on EVERY uploaded PDF — regardless of whether the rules-based
classifier recognised it — and extracts:

  document_category   — what kind of document this is
  display_name        — short human-readable label ("VicRoads Certificate")
  issuing_authority   — organisation that produced it ("VicRoads", "Monash City Council")
  document_date       — the date PRINTED ON the document (not the search date)
  quote_date          — verbatim text from the doc that contains that date
  property_address    — full address if mentioned
  key_facts           — 2–5 bullet points important for Section 32 disclosure
  is_section32_attachment — should this appear in the attachments list?

Anti-hallucination
------------------
  • document_date must be backed by quote_date (verbatim from the text)
  • Code verifies the quote exists in raw text before trusting the date
  • AI is told: if you can't find the date, return null — don't guess

This replaces the fragile rules-based approach where 12 of 16 documents
were silently ignored because the classifier returned UNKNOWN.
"""
from __future__ import annotations

import json
import re
from typing import Any

from triconvey_agent.ai.client import AIClient
from triconvey_agent.schemas.documents import Document

# Send at most this many characters to GPT-4o per document.
# Enough for 3-4 pages; longer docs are truncated after that.
_MAX_CHARS = 8_000

# ---------------------------------------------------------------------------
# Known document categories (helps GPT-4o pick a consistent value)
# ---------------------------------------------------------------------------

KNOWN_CATEGORIES = [
    "vendor_instruction_form",
    "vic_title_register_search",
    "plan_of_subdivision",
    "plan_of_consolidation",
    "transfer_instrument",
    "notice_instrument",
    "vic_roads_certificate",
    "land_tax_certificate",
    "epa_certificate",
    "council_rates_certificate",
    "council_building_approval_certificate",
    "water_information_statement",
    "water_encumbrance_certificate",
    "building_permit",
    "building_permit_application",
    "certificate_of_final_inspection",
    "occupancy_permit",
    "certificate_of_compliance_plumbing",
    "certificate_of_compliance_electrical",
    "certificate_of_compliance_gas",
    "domestic_building_warranty_insurance",
    "home_building_insurance",
    "racv_insurance",
    "owners_corporation_certificate",
    "owners_corporation_rules",
    "auction_contract",
    "mortgage_document",
    "caveat",
    "covenant",
    "section_173_agreement",
    "owners_builder_report",
    "energy_efficiency_disclosure",
    "other_government_certificate",
    "other_legal_document",
    "unknown",
]

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT = """\
You are a document analyst for an Australian property conveyancing firm (Victoria).
Read the document below and extract the key metadata.

FILENAME: {filename}

DOCUMENT TEXT:
---
{text}
---

TASK — return ONLY the JSON below (no markdown, no explanation):

{{
  "document_category": "<one of the categories listed>",
  "display_name": "<concise human label, e.g. 'VicRoads Certificate', 'Building Permit'>",
  "issuing_authority": "<organisation that issued or produced this document, null if unknown>",
  "document_date": "<date printed ON this document as dd/mm/yyyy — NOT today's date, NOT the search run date. null if genuinely not present>",
  "quote_date": "<verbatim text from the document that contains the date — must be copy-pasteable from above. null if document_date is null>",
  "property_address": "<full property address if mentioned in the document, null if not>",
  "key_facts": [
    "<important fact 1 relevant to Section 32 disclosure>",
    "<important fact 2>",
    "<add more if relevant, omit if not>"
  ],
  "is_section32_attachment": <true if this document should appear in the Section 32 disclosure attachments list, false if it is internal or not disclosable>
}}

KNOWN DOCUMENT CATEGORIES (choose the best match, or "unknown"):
{categories}

CRITICAL RULES:
1. document_date must be the date PRINTED ON the document — e.g. "Issued 7 April 2026", "Dated 07/04/2026".
   Do NOT use the date the search was ordered, or today's date, or a process date.
2. quote_date MUST be a verbatim substring from the text above that proves the date.
   If you cannot find a clear date, return null for both fields.
3. key_facts should capture things like: amount owed, permit number, compliance status,
   auction conditions, mortgage details, encumbrances, restrictions — anything relevant to
   disclosing the property's legal status to a buyer.
4. If the document is a certificate or government enquiry, is_section32_attachment is usually true.
5. NEVER invent values. Return null rather than guess."""


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse(text: str) -> dict[str, Any]:
    try:
        return json.loads(_strip_fences(text))
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"No valid JSON in AI response: {text[:200]}")


# ---------------------------------------------------------------------------
# Document summary model (plain dict — no Pydantic to stay lightweight)
# ---------------------------------------------------------------------------

def _empty_summary(doc: Document, error: str | None = None) -> dict[str, Any]:
    return {
        "source_file": doc.filename,
        "document_category": "unknown",
        "display_name": doc.filename,
        "issuing_authority": None,
        "document_date": None,
        "quote_date": None,
        "date_verified": False,
        "property_address": None,
        "key_facts": [],
        "is_section32_attachment": False,
        "ai_error": error,
    }


def _verify_date_quote(summary: dict[str, Any], raw_text: str) -> dict[str, Any]:
    """Check that quote_date actually appears verbatim in the document text."""
    quote = summary.get("quote_date")
    if not quote:
        summary["date_verified"] = False
        summary["document_date"] = None   # no quote → reject the date
        return summary

    # Exact match
    if quote in raw_text:
        summary["date_verified"] = True
        return summary

    # Normalised whitespace match
    if " ".join(quote.split()) in " ".join(raw_text.split()):
        summary["date_verified"] = True
        return summary

    # Quote not found → reject date to prevent hallucinated dates
    summary["date_verified"] = False
    summary["document_date"] = None
    return summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarise_document(doc: Document, client: AIClient) -> dict[str, Any]:
    """Run GPT-4o on a single document and return its summary dict."""
    raw_text = doc.raw_text or doc.normalized_text or ""
    if not raw_text.strip():
        return _empty_summary(doc, error="empty document text")

    prompt = _PROMPT.format(
        filename=doc.filename,
        text=raw_text[:_MAX_CHARS],
        categories="\n".join(f"  {c}" for c in KNOWN_CATEGORIES),
    )

    try:
        response = client.complete(prompt)
        raw = _parse(response.raw_text)
    except Exception as exc:
        return _empty_summary(doc, error=str(exc))

    summary: dict[str, Any] = {
        "source_file": doc.filename,
        "document_category": raw.get("document_category") or "unknown",
        "display_name": raw.get("display_name") or doc.filename,
        "issuing_authority": raw.get("issuing_authority"),
        "document_date": raw.get("document_date"),
        "quote_date": raw.get("quote_date"),
        "date_verified": False,
        "property_address": raw.get("property_address"),
        "key_facts": raw.get("key_facts") or [],
        "is_section32_attachment": bool(raw.get("is_section32_attachment", False)),
        "ai_error": None,
    }

    return _verify_date_quote(summary, raw_text)


def summarise_all_documents(
    docs: list[Document],
    client: AIClient,
) -> list[dict[str, Any]]:
    """Summarise every PDF document in the list."""
    summaries: list[dict[str, Any]] = []
    for doc in docs:
        if not doc.is_pdf:
            continue   # skip YAML form templates
        summary = summarise_document(doc, client)
        summaries.append(summary)
    return summaries
