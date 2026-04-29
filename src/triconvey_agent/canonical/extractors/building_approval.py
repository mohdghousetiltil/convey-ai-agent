from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.normalizers.display_names import normalize_council_display_name
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:building_permit_v1"
_RECENT_YEARS = 7

_DATE_DMY_WORD = re.compile(
    r"\b(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+(\d{4})\b",
    re.IGNORECASE,
)
_DATE_DMY_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_BALLARAT_BLOCK_RE = re.compile(
    r"(?P<number>[A-Z]{2,}(?:/[A-Z0-9\-]+)+)\s+(?P<description>.+?)\s+Private Permit\s+(?P<permit_date>\d{1,2}\s+[A-Za-z]+\s+\d{4})"
    r"(?:\s+Occupancy Permit\s+(?P<occupancy_date>\d{1,2}\s+[A-Za-z]+\s+\d{4}))?",
    re.IGNORECASE | re.DOTALL,
)
_INDIGO_ROW_RE = re.compile(
    r"(?P<number>\d{8,})\s+(?P<surveyor>[A-Za-z]+\s+[A-Za-z]+)\s+(?P<description>.+?)\s+(?P<permit_date>\d{1,2}/\d{1,2}/\d{4})\s+No\s+No",
    re.IGNORECASE | re.DOTALL,
)
_COUNCIL_NAME_RE = re.compile(
    r"(?m)^\s*((?:City of|Shire of)?\s*[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+(?:City Council|Shire Council|Council))\s*$"
)
_FORM2_PERMIT_NO_RE = re.compile(
    r"(?im)^\s*building\s+permit\s*$\s*(?P<number>[A-Z]{1,4}[-\s]?[A-Z]?\s*\d{3,}(?:/[0-9A-Z]+)+)\s*$"
)
_FORM2_ISSUE_DATE_RE = re.compile(
    r"(?i)date\s+of\s+issue\s+of\s+permit:\s*(?P<date>\d{1,2}\s+[A-Za-z]+\s+\d{4})"
)
_FORM2_NATURE_RE = re.compile(
    r"(?is)nature\s+of\s+building\s+work\s*(?P<desc>.+?)(?:\n\s*\*|$)"
)

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _doc_text(doc: Document) -> str:
    return doc.raw_text or doc.normalized_text or ""


def _page_for_quote(doc: Document, quote: str) -> int | None:
    needle = quote.strip()
    if not needle:
        return None
    for page in doc.pages:
        haystack = page.text or page.normalized_text or ""
        if needle in haystack:
            return page.page_number
    return None


def _make_fact(doc: Document, path: str, value: object, quote: str, *, confidence: float = 0.95) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[
            Source(
                file=doc.filename,
                page=_page_for_quote(doc, quote),
                quote=quote,
                quote_verified=True,
            )
        ],
        extractor=EXTRACTOR_NAME,
    )


def _normalize_date(text: str) -> str | None:
    match = _DATE_DMY_SLASH.search(text)
    if match:
        day, month, year = match.groups()
        return f"{int(day):02d}/{int(month):02d}/{year}"
    match = _DATE_DMY_WORD.search(text)
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    return f"{int(day):02d}/{month:02d}/{year}"


def _is_recent(date_str: str | None) -> bool:
    if not date_str:
        return False
    try:
        issued = datetime.strptime(date_str, "%d/%m/%Y").replace(tzinfo=UTC)
    except ValueError:
        return False
    cutoff = datetime.now(UTC).replace(tzinfo=UTC) - timedelta(days=365 * _RECENT_YEARS)
    return issued >= cutoff


def _normalize_description(description: str) -> str:
    cleaned = " ".join(description.split()).strip(" -")
    if cleaned.lower().startswith("construct "):
        tail = cleaned[len("construct "):].strip()
        article = "an" if tail[:1].lower() in {"a", "e", "i", "o", "u"} else "a"
        return f"Construction of {article} {tail}"
    return cleaned


def _extract_council_name(doc: Document, text: str) -> str | None:
    if match := _COUNCIL_NAME_RE.search(text):
        return normalize_council_display_name(match.group(1))
    filename = doc.filename.lower()
    if "ballarat" in filename or "ballaret" in filename:
        return "City of Ballarat"
    if "indigo" in filename:
        return "Indigo Shire Council"
    return None


def _ballarat_records(text: str) -> list[dict[str, str | bool | None]]:
    records: list[dict[str, str | bool | None]] = []
    for match in _BALLARAT_BLOCK_RE.finditer(text):
        number = match.group("number").strip()
        description = _normalize_description(match.group("description"))
        permit_date = _normalize_date(match.group("permit_date"))
        quote = " ".join(part for part in match.group(0).split())
        if permit_date:
            records.append(
                {
                    "kind": "building_permit",
                    "number": number,
                    "issue_date": permit_date,
                    "description": description,
                    "quote": quote,
                }
            )
        occupancy_date = _normalize_date(match.group("occupancy_date") or "")
        if occupancy_date:
            records.append(
                {
                    "kind": "occupancy_permit",
                    "number": number,
                    "issue_date": occupancy_date,
                    "description": "",
                    "quote": quote,
                }
            )
    return records


def _indigo_records(text: str) -> list[dict[str, str | bool | None]]:
    records: list[dict[str, str | bool | None]] = []
    for match in _INDIGO_ROW_RE.finditer(text):
        number = match.group("number").strip()
        description = _normalize_description(match.group("description"))
        issue_date = _normalize_date(match.group("permit_date"))
        quote = " ".join(part for part in match.group(0).split())
        if issue_date:
            records.append(
                {
                    "kind": "building_permit",
                    "number": number,
                    "issue_date": issue_date,
                    "description": description,
                    "quote": quote,
                }
            )
    return records


def _form2_record(text: str) -> dict[str, str | bool | None] | None:
    """Parse common VIC FORM 2 Building Permit layouts.

    Many council-issued permits follow a "FORM 2 ... BUILDING PERMIT" template
    and do not contain the tabular layouts our council-specific regexes handle.
    """
    number_match = _FORM2_PERMIT_NO_RE.search(text)
    date_match = _FORM2_ISSUE_DATE_RE.search(text)
    if not number_match and not date_match:
        return None

    number = (number_match.group("number") if number_match else "").strip()
    issue_date = _normalize_date(date_match.group("date") if date_match else "")
    nature_match = _FORM2_NATURE_RE.search(text)
    description = _normalize_description(nature_match.group("desc")) if nature_match else ""

    quote_parts = []
    if number:
        quote_parts.append(number)
    if date_match:
        quote_parts.append(date_match.group(0).strip())
    if not quote_parts and number_match:
        quote_parts.append(number_match.group(0).strip())
    quote = " | ".join(quote_parts) if quote_parts else (number or issue_date or "Building permit")

    return {
        "kind": "building_permit",
        "number": number,
        "issue_date": issue_date or "",
        "description": description,
        "quote": quote,
    }


def extract_building_approval_facts(doc: Document) -> list[Fact]:
    text = _doc_text(doc)
    if not text:
        return []
    lowered = text.lower()
    if (
        "building permit" not in lowered
        and "occupancy permit" not in lowered
        and "regulation 51" not in lowered
        and "permits, certificates of final inspection" not in lowered
    ):
        return []

    facts: list[Fact] = []
    council_name = _extract_council_name(doc, text)
    if council_name:
        facts.append(_make_fact(doc, P.RATES_COUNCIL_AUTHORITY, council_name, council_name, confidence=0.92))

    records = _ballarat_records(text)
    if not records:
        records = _indigo_records(text)
    if not records:
        form2 = _form2_record(text)
        if form2 and (form2.get("number") or form2.get("issue_date") or form2.get("description")):
            records = [form2]

    recent_records = 0
    for index, record in enumerate(records):
        quote = str(record["quote"])
        kind = str(record["kind"])
        number = str(record.get("number") or "").strip()
        issue_date = str(record.get("issue_date") or "").strip()
        description = str(record.get("description") or "").strip()
        within_last_7_years = _is_recent(issue_date)
        if within_last_7_years:
            recent_records += 1
        facts.extend(
            [
                _make_fact(doc, P.building_permit(index, "kind"), kind, quote),
                _make_fact(doc, P.building_permit(index, "number"), number, quote),
                _make_fact(doc, P.building_permit(index, "issue_date"), issue_date, quote),
                _make_fact(doc, P.building_permit(index, "description"), description, quote, confidence=0.90),
                _make_fact(doc, P.building_permit(index, "within_last_7_years"), within_last_7_years, quote),
            ]
        )

    if records:
        anchor_quote = str(records[0]["quote"])
        facts.append(_make_fact(doc, P.BUILDING_RECENT_PERMITS_EXIST, recent_records > 0, anchor_quote))
        facts.append(_make_fact(doc, P.BUILDING_RECENT_PERMITS_COUNT, recent_records, anchor_quote))

    return facts
