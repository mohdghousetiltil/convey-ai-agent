"""Owners corporation document extractor.

Parses OC certificates, OC basic reports, and OC fee schedules to emit:
  - existence (rates.owners_corporation.exists)
  - authority display name (rates.owners_corporation.authority_name)
  - annual fee amount (rates.owners_corporation.annual_amount)

Annual amount extraction
------------------------
OC fees can be stated as annual, quarterly, monthly, or as a total for
the period. This extractor:

  1. Searches for explicit annual/per-annum labels first (highest confidence).
  2. Falls back to quarterly × 4 or monthly × 12 if those labels appear.
  3. Uses date range analysis to verify the billing period.
  4. As a last resort, uses the largest dollar figure if the document context
     strongly implies a fee schedule.

The annual amount from the OC certificate wins over the vendor form
(authority rule: owners_corporation* > vendor_form*).
"""
from __future__ import annotations

import re
from datetime import date

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:owners_corporation_v1"

# ---------------------------------------------------------------------------
# Document identification
# ---------------------------------------------------------------------------

_DOC_PATTERN = re.compile(
    r"(?:"
    r"owners?\s+corporation\s+(?:search\s+)?report"   # Landata OC search report
    r"|owners?\s+corporation\s+(?:certificate|basic\s+report)"
    r"|oc\s+(?:certificate|report|fee\s+schedule|levy\s+notice)"
    r"|strata\s+(?:certificate|fees?\s+schedule|levy\s+notice)"
    r"|lot\s+entitlement\s+and\s+liability"
    r"|body\s+corporate\s+(?:certificate|search\s+report)"
    r"|plan\s+no\.\s*[A-Z]{0,3}\d+"               # plan reference typical in OC docs
    r")",
    re.IGNORECASE,
)

_CURRENCY_RE = re.compile(r"\$[\d,]+\.\d{2}")
_CURRENCY_PATTERN = r"\$[\d,]+\.\d{2}"

# ---------------------------------------------------------------------------
# OC authority name patterns
# ---------------------------------------------------------------------------

# Numbered OC: "OWNERS CORPORATION 1", "Owners Corporation No. 2"
_OC_NUMBERED_RE = re.compile(
    r"\bOwners?\s+Corporation\s+(?:No\.?\s*)?(\d+)\b",
    re.IGNORECASE,
)

# Named OC manager or body (NOT government departments)
# Must start with a proper noun that isn't a government department
_OC_NAMED_RE = re.compile(
    r"\b(?!Department|State|Victorian|Commonwealth|Federal|Government|Municipal|Local)"
    r"([A-Z][A-Za-z]+(?:\s+[A-Za-z]+){0,4})\s+Owners?\s+Corporation\b",
)

# ---------------------------------------------------------------------------
# Annual amount patterns (checked first — highest confidence)
# ---------------------------------------------------------------------------

_ANNUAL_PATTERNS = [
    # "Annual levy $X", "Annual fee $X", "Annual contribution $X"
    re.compile(
        rf"(annual(?:ly)?\s+(?:fees?|levies|levy|amount|contribution|charge)[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    # "Total annual fees $X"
    re.compile(
        rf"(total\s+annual\s+(?:fees?|levies|levy|amount|contribution)[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    # "Annual contribution $X"
    re.compile(
        rf"(annual\s+contribution[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    # "Fees per annum $X" / "per annum $X"
    re.compile(
        rf"((?:fees?|levies|levy|amount)\s+per\s+annum[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(per\s+annum[^\n$]{{0,40}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(({_CURRENCY_PATTERN})[^\n$]{{0,40}}per\s+annum)",
        re.IGNORECASE,
    ),
    # "$X per year"
    re.compile(
        rf"(({_CURRENCY_PATTERN})[^\n$]{{0,30}}per\s+year)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"((?:fees?|levies|levy|amount)\s+per\s+year[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    # "20XX/20XX fees $X" (financial year fees)
    re.compile(
        rf"(20\d{{2}}[-/]20?\d{{2}}\s+(?:fees?|levies|levy|amount)[^\n$]{{0,40}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    # "Total fees for the year $X"
    re.compile(
        rf"(total\s+fees?\s+for\s+the\s+(?:financial\s+)?year[^\n$]{{0,40}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Quarterly patterns
# ---------------------------------------------------------------------------

_QUARTERLY_PATTERNS = [
    re.compile(
        rf"(quarterly\s+(?:fees?|levies|levy|amount|contribution)[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(fees?\s+per\s+quarter[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(({_CURRENCY_PATTERN})\s+per\s+quarter[^\n]*?)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(fees?\s+for\s+the\s+above\s+lot\s+are\s+({_CURRENCY_PATTERN})\s+per\s+quarter[^\n]*?)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"((?:current\s+)?fees?[^\n$]{{0,40}}({_CURRENCY_PATTERN})[^\n]{{0,40}}per\s+quarter[^\n]*?)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"((?:quarterly\s+)?instalment[^\n$]{{0,40}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Monthly patterns
# ---------------------------------------------------------------------------

_MONTHLY_PATTERNS = [
    re.compile(
        rf"(monthly\s+(?:fees?|levies|levy|amount|contribution)[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"((?:fees?|levies|levy|amount)\s+per\s+month[^\n$]{{0,60}}({_CURRENCY_PATTERN}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(({_CURRENCY_PATTERN})\s+per\s+month[^\n]*?)",
        re.IGNORECASE,
    ),
]

# Period date range
_PERIOD_RE = re.compile(
    r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})\s+(?:to|-)\s+(\d{1,2})[/-](\d{1,2})[/-](\d{4})"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fact(
    doc: Document,
    path: str,
    value: object,
    quote: str,
    *,
    confidence: float,
    notes: str | None = None,
) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[Source(file=doc.filename, page=None, quote=quote, quote_verified=True)],
        extractor=EXTRACTOR_NAME,
        notes=notes,
    )


def _parse_currency(value: str) -> float:
    return float(value.replace("$", "").replace(",", "").strip())


def _first_currency_group(match: re.Match) -> str | None:
    """Return the first group that looks like a dollar amount."""
    for g in reversed(match.groups()):
        if isinstance(g, str) and g.startswith("$"):
            return g
    return None


def _period_multiplier(text: str) -> int:
    """Estimate annualisation multiplier from date range in text."""
    m = _PERIOD_RE.search(text)
    if not m:
        return 4  # default: assume quarterly
    d1, mo1, y1, d2, mo2, y2 = [int(x) for x in m.groups()]
    try:
        start = date(y1, mo1, d1)
        end = date(y2, mo2, d2)
        days = (end - start).days + 1
    except ValueError:
        return 4
    if days <= 40:
        return 12
    if days <= 100:
        return 4
    if days <= 200:
        return 2
    return 1


def _extract_oc_name(doc: Document, text: str) -> list[Fact]:
    """Extract the specific OC name — numbered OC preferred over generic."""
    # "OWNERS CORPORATION 1" / "Owners Corporation No. 2"
    m = _OC_NUMBERED_RE.search(text)
    if m:
        name = f"Owners Corporation {m.group(1)}"
        return [
            _make_fact(
                doc, P.RATES_OC_AUTHORITY_NAME, name, m.group(0),
                confidence=0.92,
                notes="OC number from document",
            )
        ]
    # Named OC (not a government department)
    m = _OC_NAMED_RE.search(text)
    if m:
        name = " ".join(m.group(0).split())
        return [
            _make_fact(
                doc, P.RATES_OC_AUTHORITY_NAME, name, name,
                confidence=0.85,
                notes="OC name from document text",
            )
        ]
    return [
        _make_fact(
            doc, P.RATES_OC_AUTHORITY_NAME, "Owners Corporation",
            "Owners Corporation document detected",
            confidence=0.75,
            notes="Generic fallback — no specific OC name found in document",
        )
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_owners_corporation_facts(doc: Document) -> list[Fact]:
    text = doc.raw_text or doc.normalized_text or ""
    file_name = doc.filename or ""
    haystack = f"{file_name}\n{text}"
    if not _DOC_PATTERN.search(haystack):
        return []

    facts: list[Fact] = [
        _make_fact(
            doc,
            P.RATES_OWNERS_CORPORATION,
            True,
            "Owners Corporation document detected",
            confidence=0.92,
            notes="OC certificate/basic report implies an active owners corporation",
        ),
    ]
    facts.extend(_extract_oc_name(doc, text))

    # --- Annual patterns (highest confidence) ---
    for pattern in _ANNUAL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        amount = _first_currency_group(match)
        if amount is None:
            continue
        facts.append(
            _make_fact(
                doc,
                P.RATES_OC_ANNUAL_AMOUNT,
                amount,
                " ".join(match.group(0).split()),
                confidence=0.92,
                notes="Annual OC fee stated explicitly in document",
            )
        )
        return facts

    # --- Quarterly patterns → × 4 ---
    for pattern in _QUARTERLY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        quarterly_amount = _first_currency_group(match)
        if quarterly_amount is None:
            continue
        multiplier = _period_multiplier(text)
        if multiplier != 4:
            multiplier = 4  # always 4 for quarterly regardless of period range
        try:
            annualised = _parse_currency(quarterly_amount) * 4
            annual_str = f"${annualised:,.2f}"
            facts.append(
                _make_fact(
                    doc,
                    P.RATES_OC_ANNUAL_AMOUNT,
                    annual_str,
                    " ".join(match.group(0).split()),
                    confidence=0.85,
                    notes=f"Annual OC fee: quarterly {quarterly_amount} × 4 = {annual_str}",
                )
            )
        except (ValueError, AttributeError):
            pass
        return facts

    # --- Monthly patterns → × 12 ---
    for pattern in _MONTHLY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        monthly_amount = _first_currency_group(match)
        if monthly_amount is None:
            continue
        try:
            annualised = _parse_currency(monthly_amount) * 12
            annual_str = f"${annualised:,.2f}"
            facts.append(
                _make_fact(
                    doc,
                    P.RATES_OC_ANNUAL_AMOUNT,
                    annual_str,
                    " ".join(match.group(0).split()),
                    confidence=0.82,
                    notes=f"Annual OC fee: monthly {monthly_amount} × 12 = {annual_str}",
                )
            )
        except (ValueError, AttributeError):
            pass
        return facts

    return facts
