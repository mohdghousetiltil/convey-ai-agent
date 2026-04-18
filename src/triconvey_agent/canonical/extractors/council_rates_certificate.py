"""Council rates certificate / land information statement extractor.

Handles Victorian council rates notices, land information statements, and
municipal rates assessments issued by local councils.

Annual amount extraction
------------------------
Priority order for the annual amount:
  1. Explicit "Annual Council Rates and Charges YYYY/YYYY $X" label  (conf 0.99)
  2. "Annual rates/charges" or "Total annual" label                   (conf 0.97)
  3. Quarterly instalment × 4                                         (conf 0.90)
  4. Half-yearly instalment × 2                                       (conf 0.88)
  5. Monthly instalment × 12                                          (conf 0.85)
  6. Sum of all charge lines levied on the same date                  (conf 0.80)

Authority name priority:
  1. "City/Shire/Borough of X" or "X City/Shire Council" in text     (conf 0.97)
  2. Council domain from website/email (e.g. brimbank.vic.gov.au)    (conf 0.90)
  3. Filename-derived (last resort)                                   (conf 0.70)

Registered as `rule:council_rates_certificate_v1` which matches the
`rule:council_rates_certificate*` glob in DEFAULT_AUTHORITY_RULES.
"""
from __future__ import annotations

import re
from datetime import date

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:council_rates_certificate_v1"

# ---------------------------------------------------------------------------
# Document identification — be very specific to avoid triggering on building
# approvals, planning certs or water bills that also mention "council".
# ---------------------------------------------------------------------------

_DOC_TRIGGERS = re.compile(
    r"(?:"
    r"land\s+information\s+certificate"          # Brimbank LIC, Section 121
    r"|council\s+rates?\s+(?:notice|assessment|statement|certificate)"
    r"|annual\s+rates?\s+and\s+charges"          # Brimbank annual rates notice
    r"|rates?\s+and\s+charges\s+for\s+(?:the\s+)?period"  # period-based notice
    r"|municipal\s+rates?\s+(?:assessment|notice|certificate)"
    r"|local\s+government\s+(?:act|rates?\s+notice)"
    r"|section\s+121\s+of\s+the\s+local\s+government"  # LIC legal reference
    r"|rates?\s+(?:notice|assessment|certificate)\s*\n"  # prominent title
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Council / authority name patterns
# ---------------------------------------------------------------------------

# "City of Melbourne", "Shire of Yarra Ranges", "Borough of Queenscliffe"
_COUNCIL_NAME_RE = re.compile(
    r"\b((?:City|Shire|Borough|Town|Rural\s+City)\s+of\s+[A-Za-z][A-Za-z\s]+?)(?:\s*\n|\s{2,}|,|\.|$)",
    re.IGNORECASE,
)

# "Yarra City Council", "Monash City Council", "Brimbank City Council"
# Negative lookahead prevents "Council" itself starting the match.
_COUNCIL_SUFFIX_RE = re.compile(
    r"\b(?!Council\b|Government\b|Local\b|State\b)"
    r"([A-Z][A-Za-z]+(?:\s+[A-Za-z]+){0,2})"
    r"\s+(City|Shire|Borough|Town|Rural\s+City)\s+Council\b",
)

# Council domain from website or email: "brimbank.vic.gov.au" → Brimbank
_COUNCIL_DOMAIN_RE = re.compile(
    r"(?:www\.)?([a-z][a-z0-9]+)\.vic\.gov\.au",
    re.IGNORECASE,
)

# Words to skip when parsing filenames
_FILENAME_STOP_WORDS = frozenset({
    "vic", "vic_", "enquiry", "certificate", "rates", "council", "notice",
    "statement", "cert", "land", "info", "information", "pdf", "doc",
    "file", "form", "annual", "report", "2025", "2026", "2024", "2023",
    "brimbank",  # we'll pick this up properly from domain/text, not just filename prefix
})

# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------

_CURRENCY_RE = re.compile(r"(?<![−\-])\$[\d,]+\.\d{2}")  # exclude negative amounts
_CURRENCY_PLAIN_RE = re.compile(r"\b([\d,]+\.\d{2})\b")


def _parse_dollar(s: str) -> float:
    return float(s.replace("$", "").replace(",", "").strip())


def _fmt(amount: float) -> str:
    return f"${amount:,.2f}"


# ---------------------------------------------------------------------------
# Annual amount patterns — ordered by priority
# ---------------------------------------------------------------------------

# 1. "Annual Council Rates and Charges 2025/2026 $1,498.57"  (Brimbank-style)
_ANNUAL_COUNCIL_RATES_RE = re.compile(
    r"Annual\s+Council\s+Rates?\s+(?:and\s+Charges?[^\n$]{0,30})?(\$[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# 2. Generic annual labels
_ANNUAL_LABELS = [
    re.compile(
        r"(?:total\s+annual\s+(?:rates?|charges?|amount|levy)"
        r"|annual\s+(?:rates?|charges?|amount|levy)\s+(?:total|due|payable)"
        r"|annual\s+(?:rates?|levy)\s+levied"
        r"|net\s+annual\s+rates?"
        r")[^\n$]{0,60}(\$[\d,]+\.\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"rates?\s+(?:and\s+charges?\s+)?for\s+(?:the\s+)?(?:full\s+)?year[^\n$]{0,40}(\$[\d,]+\.\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"total\s+rates?\s+(?:and\s+charges?\s+)?(?:levied|for\s+the\s+year)[^\n$]{0,40}(\$[\d,]+\.\d{2})",
        re.IGNORECASE,
    ),
]

# 3. Quarterly instalment labels
_QUARTERLY_LABELS = re.compile(
    r"(?:"
    r"(?:1st|2nd|3rd|4th|first|second|third|fourth)\s+(?:quarter(?:ly)?|instalment|installment)"
    r"|(?:quarter(?:ly)?|instalment|installment)\s+(?:1|2|3|4|one|two|three|four)"
    r"|quarterly\s+(?:rates?|amount|charge|instalment)"
    r"|(?:Q[1-4])\s+(?:instalment|installment|rates?)"
    r"|amount\s+(?:due|payable)\s+this\s+quarter"
    r")"
    r"[^\n$]{0,60}(\$[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# 4. Half-yearly instalment labels
_HALFYEARLY_LABELS = re.compile(
    r"(?:"
    r"half[- ]?(?:year(?:ly)?|annual)\s+(?:instalment|installment|rates?|amount)"
    r"|(?:instalment|installment)\s+(?:1\s+of\s+2|2\s+of\s+2)"
    r"|semi[- ]annual\s+(?:rates?|instalment|amount)"
    r")"
    r"[^\n$]{0,60}(\$[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# 5. Monthly instalment
_MONTHLY_LABELS = re.compile(
    r"(?:monthly\s+(?:instalment|installment|rates?|amount))"
    r"[^\n$]{0,60}(\$[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# 6. Sum of individual charge lines (each levied on the same annual date)
# Pattern: "label  Date Levied DD/MM/YYYY  $amount"
_ANNUAL_CHARGE_LINE_RE = re.compile(
    r"^(.+?)\s+Date\s+Levied\s+\d{1,2}[/\-]\d{1,2}[/\-]\d{4}\s+(\$[\d,]+\.\d{2})",
    re.MULTILINE | re.IGNORECASE,
)

# Period date range for multiplier verification
_PERIOD_RE = re.compile(
    r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})\s+(?:to|-)\s+(\d{1,2})[/-](\d{1,2})[/-](\d{4})"
)

# Lines to EXCLUDE from charge sum (arrears, payments, interest, rebates, adjustments)
_EXCLUDE_CHARGE_LABELS = re.compile(
    r"(?:arrears|interest|payment|rebate|adjustment|balance|credit|esvf|emergency\s+services)",
    re.IGNORECASE,
)


def _period_multiplier_from_range(text: str) -> int | None:
    m = _PERIOD_RE.search(text)
    if not m:
        return None
    d1, mo1, y1, d2, mo2, y2 = [int(x) for x in m.groups()]
    try:
        start = date(y1, mo1, d1)
        end = date(y2, mo2, d2)
        days = (end - start).days + 1
    except ValueError:
        return None
    if days <= 100:
        return 4
    if days <= 200:
        return 2
    if days <= 380:
        return 1
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc_text(doc: Document) -> str:
    return doc.raw_text or doc.normalized_text or ""


def _make_source(doc: Document, quote: str) -> Source:
    return Source(file=doc.filename, page=None, quote=quote, quote_verified=True)


def _make_fact(
    doc: Document,
    path: str,
    value: object,
    quote: str,
    *,
    confidence: float = 0.97,
    notes: str | None = None,
) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[_make_source(doc, quote)],
        extractor=EXTRACTOR_NAME,
        notes=notes,
    )


def _compact(s: str) -> str:
    return " ".join(s.split())


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------


def _extract_council_name(doc: Document, text: str, filename: str) -> list[Fact]:
    """Try multiple patterns to extract the council authority name."""

    # 1. "X City/Shire Council" in text (e.g. "Brimbank City Council")
    m = _COUNCIL_SUFFIX_RE.search(text)
    if m:
        name = _compact(m.group(0))
        # Normalize ALL-CAPS to title case
        if name == name.upper():
            name = name.title()
        if len(name) > 4:
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_AUTHORITY, name, name,
                    confidence=0.97,
                    notes="council name from document text",
                )
            ]

    # 2. "City/Shire of X" in text
    m = _COUNCIL_NAME_RE.search(text)
    if m:
        name = _compact(m.group(1))
        if name == name.upper():
            name = name.title()
        if len(name) > 4:
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_AUTHORITY, name, _compact(m.group(0)),
                    confidence=0.97,
                    notes="council name from document text",
                )
            ]

    # 3. Council domain from website/email (e.g. brimbank.vic.gov.au → Brimbank City Council)
    m = _COUNCIL_DOMAIN_RE.search(text)
    if m:
        domain_name = m.group(1).title()
        # Avoid generic domain names
        if domain_name.lower() not in {"vic", "gov", "au", "landata", "sro"}:
            name = f"{domain_name} City Council"
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_AUTHORITY, name,
                    f"council domain: {m.group(0)}",
                    confidence=0.90,
                    notes="council name inferred from website/email domain",
                )
            ]

    # 4. Filename-derived: skip common prefix words, use first meaningful token
    fn_lower = filename.lower()
    tokens = re.split(r"[_\-\s/\\.]", fn_lower)
    for token in tokens:
        token = token.strip("()")
        if len(token) > 3 and token not in _FILENAME_STOP_WORDS:
            name = token.title() + " City Council"
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_AUTHORITY, name,
                    f"filename: {filename}",
                    confidence=0.65,
                    notes="council name inferred from filename — verify manually",
                )
            ]

    return []


def _extract_annual_amount(doc: Document, text: str) -> list[Fact]:
    """Extract the annual council rates amount — never use outstanding/payment amounts."""

    # 1. "Annual Council Rates and Charges 2025/2026 $1,498.57"
    m = _ANNUAL_COUNCIL_RATES_RE.search(text)
    if m:
        amount_str = m.group(1)
        return [
            _make_fact(
                doc, P.RATES_COUNCIL_ANNUAL, amount_str,
                _compact(m.group(0)),
                confidence=0.99,
                notes="Annual council rates — stated explicitly as 'Annual Council Rates and Charges'.",
            )
        ]

    # 2. Generic annual labels
    for pattern in _ANNUAL_LABELS:
        m = pattern.search(text)
        if m:
            amount_str = m.group(1)
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, amount_str,
                    _compact(m.group(0)),
                    confidence=0.97,
                    notes="Annual council rates — explicit annual label in document.",
                )
            ]

    # 3. Quarterly instalment × 4
    m = _QUARTERLY_LABELS.search(text)
    if m:
        instalment_str = m.group(1)
        multiplier = _period_multiplier_from_range(text) or 4
        try:
            annual = round(_parse_dollar(instalment_str) * multiplier, 2)
            annual_str = _fmt(annual)
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, annual_str,
                    _compact(m.group(0)),
                    confidence=0.90,
                    notes=f"Annual council rates: quarterly {instalment_str} × {multiplier} = {annual_str}",
                )
            ]
        except (ValueError, AttributeError):
            pass

    # 4. Half-yearly instalment × 2
    m = _HALFYEARLY_LABELS.search(text)
    if m:
        instalment_str = m.group(1)
        multiplier = _period_multiplier_from_range(text) or 2
        try:
            annual = round(_parse_dollar(instalment_str) * multiplier, 2)
            annual_str = _fmt(annual)
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, annual_str,
                    _compact(m.group(0)),
                    confidence=0.88,
                    notes=f"Annual council rates: half-yearly {instalment_str} × {multiplier} = {annual_str}",
                )
            ]
        except (ValueError, AttributeError):
            pass

    # 5. Monthly instalment × 12
    m = _MONTHLY_LABELS.search(text)
    if m:
        instalment_str = m.group(1)
        try:
            annual = round(_parse_dollar(instalment_str) * 12, 2)
            annual_str = _fmt(annual)
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, annual_str,
                    _compact(m.group(0)),
                    confidence=0.85,
                    notes=f"Annual council rates: monthly {instalment_str} × 12 = {annual_str}",
                )
            ]
        except (ValueError, AttributeError):
            pass

    # 6. Sum of "Date Levied" charge lines (excludes arrears/payments/ESVF)
    charge_matches = list(_ANNUAL_CHARGE_LINE_RE.finditer(text))
    if charge_matches:
        total = 0.0
        included = []
        for cm in charge_matches:
            label = cm.group(1).strip()
            if _EXCLUDE_CHARGE_LABELS.search(label):
                continue
            try:
                total += _parse_dollar(cm.group(2))
                included.append(f"{label}: {cm.group(2)}")
            except (ValueError, AttributeError):
                pass
        if total > 0 and len(included) >= 2:
            annual_str = _fmt(total)
            quote = f"Sum of annual rate lines: {'; '.join(included[:4])}"
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, annual_str,
                    quote,
                    confidence=0.80,
                    notes=(
                        f"Annual council rates: sum of {len(included)} 'Date Levied' charge "
                        f"lines (excluding arrears, payments, ESVF) = {annual_str}"
                    ),
                )
            ]

    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_council_rates_certificate_facts(doc: Document) -> list[Fact]:
    """Extract facts from a council rates notice or land information statement."""
    text = _doc_text(doc)
    if not text:
        return []

    filename = doc.filename or ""
    haystack = f"{filename}\n{text}"

    if not _DOC_TRIGGERS.search(haystack):
        return []

    facts: list[Fact] = []
    facts.extend(_extract_council_name(doc, text, filename))
    facts.extend(_extract_annual_amount(doc, text))
    return facts
