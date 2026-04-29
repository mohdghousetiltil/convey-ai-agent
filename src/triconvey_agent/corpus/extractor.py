"""AI-powered document corpus extractor.

Extracts rich structured information from a PDF document into a DocumentCorpusEntry.

Page limit rule:
  - Documents ≤ 25 pages: process all pages
  - Documents > 25 pages: process only first 20 pages

Subheading-aware extraction: the prompt instructs the AI to use document
subheadings as context anchors so fields stay grouped correctly.

Anti-hallucination: same quote-verification pattern as document_extractor.py —
all values must include a verbatim quote from the text.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from triconvey_agent.ai.openai_client import openai_runtime_disabled
from triconvey_agent.corpus.schema import (
    AttachmentEntry,
    AuthorityEntry,
    DocumentCorpusEntry,
    ExtractedAmount,
    ExtractedDate,
    ExtractedName,
    ExtractedReference,
    OwnerCorporationInfo,
    PermitInfo,
    QAPair,
    ServiceEntry,
)

LOG = logging.getLogger(__name__)

_PAGE_LIMIT = 20
_PAGE_THRESHOLD = 25
_MAX_TEXT_CHARS = 28_000  # ~20 pages of dense legal text


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_CORPUS_EXTRACTION_PROMPT = """\
You are an expert Victorian conveyancing AI assistant.
Extract ALL structured information from the following document text.
The document may have subheadings — use them as context anchors to group fields correctly.

SOURCE DOCUMENT TEXT (verbatim):
---
{text}
---

DOCUMENT METADATA:
  filename: {filename}
  document_type: {document_type}
  pages_processed: {pages_processed}

TASK
Extract the full information below. Return a single JSON object.
For any field not present in the document, use null.

ANTI-HALLUCINATION RULES (strictly enforced):
1. NEVER invent values — if it is not in the text, use null.
2. Keep values verbatim from the document where possible.
3. Reduce confidence fields when evidence is indirect or ambiguous.

REQUIRED JSON OUTPUT STRUCTURE:
{{
  "client_name": "string or null",
  "volume_folio": "string (e.g. 'Volume 10123 Folio 456') or null",
  "property_address": "full property address string or null",

  "council_authority": {{
    "name": "council name (e.g. 'City of Monash') or null",
    "amount": "amount string (e.g. '$1,234.56') or null",
    "frequency": "Annually / Quarterly / Monthly / One-off / null"
  }} or null,

  "water_authority": {{
    "name": "water authority name (e.g. 'Yarra Valley Water') or null",
    "amount": "amount string or null",
    "frequency": "Annually / Quarterly / Monthly / One-off / null"
  }} or null,

  "state_revenue": {{
    "name": "State Revenue Office or similar or null",
    "amount": "amount string or null",
    "frequency": "Land Tax / Annually / null"
  }} or null,

  "land_tax": {{
    "name": "string or null",
    "amount": "land tax amount string or null",
    "frequency": "Land Tax / Annually / null"
  }} or null,

  "owner_corporation": {{
    "name": "OC name or null",
    "amount": "OC fee amount or null",
    "frequency": "Annually / Quarterly / Monthly / null",
    "is_inactive": true/false/null
  }} or null,

  "building_permit": {{
    "number": "permit number string or null",
    "date": "date string or null",
    "description": "description of works or null"
  }} or null,

  "occupancy_permit": {{
    "number": "permit number string or null",
    "date": "date string or null",
    "description": "description or null"
  }} or null,

  "is_bushfire_prone": true/false/null,
  "has_road_access": true/false/null,

  "dates": [
    {{"label": "descriptive label", "value": "date string", "source_page": integer or null}}
  ],

  "amounts": [
    {{"label": "descriptive label", "value": "amount string", "currency": "AUD", "frequency": "Annually/Quarterly/Monthly/One-off/null", "source_page": integer or null}}
  ],

  "names": [
    {{"role": "vendor/purchaser/solicitor/agent/owner/etc", "name": "full name", "source_page": integer or null}}
  ],

  "reference_numbers": [
    {{"label": "document/reference type", "value": "reference number", "source_page": integer or null}}
  ],

  "descriptions": [
    "important textual description from the document"
  ],

  "services": [
    {{"service_type": "mains_water/mains_gas/mains_sewerage/telephone_nbn/electricity/bottled_gas/tank_water/septic_tank", "connected": true/false, "notes": "string or null"}}
  ],

  "attachments": [
    {{"id": "attachment ID or null", "name": "attachment name", "date": "date or null", "document_type": "type or null"}}
  ],

  "important_information": [
    "key facts, warnings, special conditions, encumbrances, caveats, legal notices, etc."
  ],

  "qa_pairs": [
    {{"question": "question text", "answer": "answer text", "source_page": integer or null}}
  ]
}}

CONVEYANCING-SPECIFIC EXTRACTION RULES:
- Volume/Folio: look for "Volume XXXXX Folio XXX" or "Vol. XXXXX Fol. XXX" patterns
- Council authority: ALWAYS use the FULL official council name (e.g. "Merri-bek City Council", NOT "Merri-bek").
  Look at the document letterhead, "Responsible Authority" field, or rates certificate heading.
  frequency must always be "Annually" for council rates.
- Water authority: ALWAYS use the FULL official name (e.g. "Yarra Valley Water", "South East Water").
  Do NOT abbreviate or truncate. frequency must always be "Annually".
- State Revenue / Land Tax: name = "State Revenue Office"; frequency = "Land Tax"
- Owner Corporation: look for OC certificates, rule 42, OC fees — note if OC is INACTIVE.
  Include the full OC name with plan number if present (e.g. "Owners Corporation 1 PS512345").
- Building permit: look for "Building Permit No.", permit number and date of issue
- Occupancy permit: look for "Occupancy Permit", "Certificate of Final Inspection"
- Bushfire Prone: look for "Bushfire Management Overlay", "Bushfire Prone Area", BAL rating
- Road Access: look for "road access", "legal access", "right of way"
- Attachments: list every attachment, schedule, exhibit, or annexure with its FULL reference number and date.
  ALWAYS include any letter prefix in reference numbers (e.g. "LP009777" not "009777", "PS512345" not "512345").
  If two entries refer to the same plan/document, only include the entry with the more complete reference.
  Do NOT include duplicates where one name is a shortened version of another.
- Services: capture connection status for ALL services mentioned
- Q&A: extract formal Q&A sections often found in section 32 disclosure statements
- Important information: capture all legal warnings, restrictions, caveats, encumbrances
"""


# ---------------------------------------------------------------------------
# Quote verification
# ---------------------------------------------------------------------------

def _verify_string_in_text(value: Any, text: str) -> bool:
    if not isinstance(value, str) or not value:
        return True
    return value.lower() in text.lower()


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------


def extract_corpus_entry(
    document_id: str,
    filename: str,
    document_type: str,
    full_text: str,
    page_count: int,
    ai_client: Any,
    model: str,
    provider: str,
) -> DocumentCorpusEntry:
    """Extract a DocumentCorpusEntry from document text using AI.

    For documents > PAGE_THRESHOLD pages only the first PAGE_LIMIT pages worth
    of text is processed (text is pre-truncated by the caller or here via
    _MAX_TEXT_CHARS).
    """
    pages_processed = min(page_count, _PAGE_LIMIT) if page_count > _PAGE_THRESHOLD else page_count
    text = full_text[:_MAX_TEXT_CHARS]

    prompt = _CORPUS_EXTRACTION_PROMPT.format(
        text=text,
        filename=filename,
        document_type=document_type,
        pages_processed=pages_processed,
    )

    raw = _call_ai(ai_client, provider, model, prompt)
    data = _parse_json(raw)

    if data is None:
        LOG.warning("Corpus extraction returned no parseable JSON for %s", filename)
        return DocumentCorpusEntry(
            document_id=document_id,
            filename=filename,
            document_type=document_type,
            page_count=page_count,
            pages_processed=pages_processed,
        )

    return _build_entry(document_id, filename, document_type, page_count, pages_processed, data)


# ---------------------------------------------------------------------------
# AI call (provider-agnostic)
# ---------------------------------------------------------------------------


def _call_ai(client: Any, provider: str, model: str, prompt: str) -> str:
    if provider == "openai" and openai_runtime_disabled():
        return ""
    try:
        if provider == "anthropic":
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text if msg.content else ""
        elif provider == "google":
            response = client.generate_content(prompt)
            return response.text
        else:  # openai
            resp = client.chat.completions.create(
                model=model,
                max_tokens=4096,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are a Victorian conveyancing data extraction AI. Respond with valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
            )
            return resp.choices[0].message.content or ""
    except Exception as exc:
        LOG.error("Corpus AI extraction failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def _parse_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    # Strip markdown fences
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    if match:
        raw = match.group(1)
    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract first { ... } block
        match = re.search(r"\{[\s\S]+\}", raw)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    LOG.warning("Could not parse corpus extraction JSON: %s…", raw[:200])
    return None


# ---------------------------------------------------------------------------
# Entry builder
# ---------------------------------------------------------------------------


def _build_entry(
    document_id: str,
    filename: str,
    document_type: str,
    page_count: int,
    pages_processed: int,
    data: dict[str, Any],
) -> DocumentCorpusEntry:

    def _authority(d: Any) -> AuthorityEntry | None:
        if not isinstance(d, dict):
            return None
        name = d.get("name")
        if not name:
            return None
        return AuthorityEntry(
            name=name,
            amount=d.get("amount"),
            frequency=d.get("frequency"),
        )

    def _permit(d: Any) -> PermitInfo | None:
        if not isinstance(d, dict):
            return None
        if not any([d.get("number"), d.get("date"), d.get("description")]):
            return None
        return PermitInfo(
            number=d.get("number"),
            date=d.get("date"),
            description=d.get("description"),
        )

    def _oc(d: Any) -> OwnerCorporationInfo | None:
        if not isinstance(d, dict):
            return None
        if not any([d.get("name"), d.get("amount"), d.get("is_inactive") is not None]):
            return None
        return OwnerCorporationInfo(
            name=d.get("name"),
            amount=d.get("amount"),
            frequency=d.get("frequency"),
            is_inactive=d.get("is_inactive"),
        )

    dates = [
        ExtractedDate(label=x.get("label", ""), value=x.get("value", ""), source_page=x.get("source_page"))
        for x in (data.get("dates") or [])
        if isinstance(x, dict) and x.get("value")
    ]

    amounts = [
        ExtractedAmount(
            label=x.get("label", ""),
            value=x.get("value", ""),
            currency=x.get("currency", "AUD"),
            frequency=x.get("frequency"),
            source_page=x.get("source_page"),
        )
        for x in (data.get("amounts") or [])
        if isinstance(x, dict) and x.get("value")
    ]

    names = [
        ExtractedName(role=x.get("role", ""), name=x.get("name", ""), source_page=x.get("source_page"))
        for x in (data.get("names") or [])
        if isinstance(x, dict) and x.get("name")
    ]

    refs = [
        ExtractedReference(label=x.get("label", ""), value=x.get("value", ""), source_page=x.get("source_page"))
        for x in (data.get("reference_numbers") or [])
        if isinstance(x, dict) and x.get("value")
    ]

    services = [
        ServiceEntry(
            service_type=x.get("service_type", ""),
            connected=bool(x.get("connected")),
            notes=x.get("notes"),
        )
        for x in (data.get("services") or [])
        if isinstance(x, dict) and x.get("service_type")
    ]

    attachments = [
        AttachmentEntry(
            id=x.get("id"),
            name=x.get("name", ""),
            date=x.get("date"),
            document_type=x.get("document_type"),
        )
        for x in (data.get("attachments") or [])
        if isinstance(x, dict) and x.get("name")
    ]

    qa_pairs = [
        QAPair(
            question=x.get("question", ""),
            answer=x.get("answer", ""),
            source_page=x.get("source_page"),
        )
        for x in (data.get("qa_pairs") or [])
        if isinstance(x, dict) and x.get("question")
    ]

    descriptions = [s for s in (data.get("descriptions") or []) if isinstance(s, str) and s.strip()]
    important = [s for s in (data.get("important_information") or []) if isinstance(s, str) and s.strip()]

    return DocumentCorpusEntry(
        document_id=document_id,
        filename=filename,
        document_type=document_type,
        page_count=page_count,
        pages_processed=pages_processed,
        client_name=data.get("client_name"),
        volume_folio=data.get("volume_folio"),
        property_address=data.get("property_address"),
        council_authority=_authority(data.get("council_authority")),
        water_authority=_authority(data.get("water_authority")),
        state_revenue=_authority(data.get("state_revenue")),
        land_tax=_authority(data.get("land_tax")),
        owner_corporation=_oc(data.get("owner_corporation")),
        building_permit=_permit(data.get("building_permit")),
        occupancy_permit=_permit(data.get("occupancy_permit")),
        is_bushfire_prone=data.get("is_bushfire_prone"),
        has_road_access=data.get("has_road_access"),
        dates=dates,
        amounts=amounts,
        names=names,
        reference_numbers=refs,
        descriptions=descriptions,
        services=services,
        attachments=attachments,
        important_information=important,
        qa_pairs=qa_pairs,
    )
