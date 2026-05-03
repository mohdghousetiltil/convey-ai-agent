"""Council rates certificate / land information statement extractor.

Handles Victorian council rates notices, land information statements, and
municipal rates assessments issued by local councils.

Annual amount extraction
------------------------
Priority order for the annual amount:
  1. Amount paid + outstanding amount (Land Information Certificate)  (conf 0.97)
     Brimbank format: "Less Payments: -$X" + "Total Rates & Charges Due: $Y"
     Generic format:  "Amount Paid $X" + "Outstanding Amount $Y"
     Knox format:     "Less Payments received $-X" + "Total balance payable $Y"
  2. Explicit "Sub total" or "Current Total" (annual levy before payments) (conf 0.96)
     Knox format:     "Sub total $2,505.90"
     Ballarat format: "Current Total:2,349.87"
  3. Explicit "Annual Council Rates and Charges YYYY/YYYY $X" label  (conf 0.88)
  4. "Annual rates/charges" or "Total annual" label                   (conf 0.84)
  5. Sum of all charge lines levied on the same date                  (conf 0.88)
  6. Quarterly instalment × 4                                         (conf 0.85)
  7. Half-yearly instalment × 2                                       (conf 0.83)
  8. Monthly instalment × 12                                          (conf 0.80)

Vendor authority is a fallback only — used when no land information
certificate has been uploaded.

Authority name priority:
  1. "City/Shire/Borough of X" or "X City/Shire Council" in text     (conf 0.97)
  2. Council domain from website/email (e.g. brimbank.vic.gov.au)    (conf 0.90)
  3. Filename-derived (last resort)                                   (conf 0.70)

Registered as `rule:council_rates_certificate_v1` which matches the
`rule:council_rates_certificate*` glob in DEFAULT_AUTHORITY_RULES.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.extractors.council_rates_row_classifier import (
    annual_from_classified_rows,
    classify_rows_with_llm,
    extract_all_money_rows,
)
from triconvey_agent.canonical.extractors.omni_perception import (
    annual_from_omni_rows,
    classify_table_with_omni,
    should_call_omni,
    validate_omni_candidate,
    verify_extraction,
    write_debug_log,
)
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

# Water authority documents often contain a "Rates Certificate" section —
# exclude them so we don't produce a fake council name from the water PDF.
_WATER_AUTHORITY_EXCLUSION = re.compile(
    r"Water\s+Information\s+Statement"
    r"|Statement\s+under\s+Section\s+158"
    r"|Section\s+158\s+Statement"
    r"|Section\s+158\s+of\s+the\s+Water\s+Act"
    r"|Information\s+Statement\s+Applications"
    r"|Total\s+annual\s+service\s+fees"
    r"|Total\s+for\s+This\s+Property"
    r"|Charges\s+levied\s+for\s+billing\s+period"
    r"|Gippsland\s+Water\s+billing\s+periods"
    r"|Date\s+of\s+Issue:\s+\d{1,2}/\d{1,2}/\d{4}.*Certificate\s+No",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Council / authority name patterns
# ---------------------------------------------------------------------------

# "City of Melbourne", "Shire of Yarra Ranges", "Borough of Queenscliffe"
# Each name word MUST start with an uppercase letter — this stops the match
# before lowercase connector words like "together", "pursuant", "and", etc.
# Handles both Title Case ("City of Ballarat") and ALL CAPS ("CITY OF BALLARAT").
_COUNCIL_NAME_RE = re.compile(
    r"\b((?:City|Shire|Borough|Town|Rural\s+City)\s+of\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})"
)

# "Yarra City Council", "Monash City Council", "Brimbank City Council"
# Negative lookahead prevents "Council" itself starting the match.
# Title-case council names: "Brimbank City Council", "Yarra Shire Council"
_COUNCIL_SUFFIX_RE = re.compile(
    r"\b(?!Council\b|Government\b|Local\b|State\b)"
    r"([A-Z][A-Za-z'-]+(?:\s+[A-Za-z'-]+){0,2})"
    r"\s+(City|Shire|Borough|Town|Rural\s+City)\s+Council\b",
)

# ALL-CAPS council names: "INDIGO SHIRE COUNCIL", "BRIMBANK CITY COUNCIL"
_COUNCIL_SUFFIX_CAPS_RE = re.compile(
    r"\b([A-Z]{2,}(?:[\s-]+[A-Z]+){0,2})"
    r"\s+(CITY|SHIRE|BOROUGH|TOWN)\s+COUNCIL\b",
)

# Council domain from website or email: "brimbank.vic.gov.au" → Brimbank
_COUNCIL_DOMAIN_RE = re.compile(
    r"(?:www\.)?([a-z][a-z0-9]+)\.vic\.gov\.au",
    re.IGNORECASE,
)

_COUNCIL_DOMAIN_SUFFIX = {
    "cardinia": "Shire Council",
    "bawbaw": "Shire Council",
    "basscoast": "Shire Council",
    "merri-bek": "City Council",
    "monash": "City Council",
    "brimbank": "City Council",
}

# Strip department-name prefixes that appear before the actual council name.
# e.g. "Revenue Services Maroondah City Council" → "Maroondah City Council"
_COUNCIL_DEPT_PREFIX_RE = re.compile(
    r"^(?:Revenue\s+Services?\s+"
    r"|Customer\s+Services?\s+"
    r"|Rates?\s+(?:and\s+)?(?:Services?\s+|Team\s+)?"
    r"|Finance\s+(?:and\s+)?(?:Services?\s+)?"
    r"|Administration\s+(?:Services?\s+)?"
    r"|Corporate\s+Services?\s+"
    r"|Council\s+Services?\s+"
    r")",
    re.IGNORECASE,
)

# Words to skip when parsing filenames
_FILENAME_STOP_WORDS = frozenset({
    "vic", "vic_", "enquiry", "certificate", "rates", "council", "notice",
    "statement", "cert", "land", "info", "information", "pdf", "doc",
    "file", "form", "annual", "report", "2025", "2026", "2024", "2023",
    "brimbank",  # we'll pick this up properly from domain/text, not just filename prefix
})

_FILENAME_COUNCIL_RE = re.compile(
    r"(?:^|[_\-\s])(?:enquiry|inquiry)\s*[-_ ]+\s*([A-Za-z][A-Za-z\s]+?)\s*[-_ ]+\s*land\s+information\s+certificate",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Annual amount: paid + outstanding helpers
# ---------------------------------------------------------------------------
#
# Victorian LICs use three different table layouts — all follow the same logic:
#   Annual total = amount already paid this FY + balance still owing
#
#  FORMAT A — Brimbank:
#    "Less Payments:  -$1,208.97"   ← paid
#    "Total Rates & Charges Due: $507.78"  ← outstanding
#    Annual = $1,208.97 + $507.78 = $1,716.75
#
#  FORMAT B — Ballarat (City of Ballarat):
#    "Less Payments Received-2,349.87"  ← paid (no $ sign, no space before -)
#    "TOTAL OUTSTANDING 0.00"           ← outstanding
#    Annual = $2,349.87 + $0.00 = $2,349.87
#    (When nothing is paid yet: "Less Payments Received 0.00", "TOTAL OUTSTANDING 1,816.67")
#
#  FORMAT C — Indigo Shire:
#    "Payments made against current year property rates: -$2,716.97"  ← paid
#    "Total: $905.00"   ← outstanding (balance remaining)
#    Annual = $2,716.97 + $905.00 = $3,621.97
#
#  FORMAT D — Maroondah City Council:
#    "Less Payments  -1,150.35"  ← paid (no $, negative in Balance column)
#    "ASSESSMENT TOTAL $1,148.00"  ← outstanding
#    Annual = $1,150.35 + $1,148.00 = $2,298.35
#
# ALSO: "Current Total:2,349.87" (Ballarat) = the annual total BEFORE payments,
# used as a direct annual amount (priority 2 below paid+outstanding).

# Keyword patterns — used to find the start of each field, then the dollar
# amount is captured by scanning the next N characters.
_PAID_KW_RE = re.compile(
    r"Less\s+Payments?\s*(?:Received|Made|to\s+Date)?"   # Brimbank / Ballarat
    r"|Payments?\s+(?:Received|Made|to\s+Date)"          # Ballarat alt
    r"|Payments?\s+made\s+against\s+current\s+year"      # Indigo
    r"|Amount\s+(?:Already\s+)?Paid"                     # generic
    r"|Credit\s+Applied"                                  # some councils
    r"|Amounts?\s+Credited"                               # some councils
    r"|Previously\s+Paid"                                 # generic
    r"|Less\s+Amount\s+(?:Paid|Credited|Received)",      # generic
    re.IGNORECASE,
)

_OUTSTANDING_KW_RE = re.compile(
    r"Total\s+Rates?\s*(?:&|and)\s*Charges?\s+Due"       # Brimbank
    r"|TOTAL\s+OUTSTANDING"                               # Ballarat
    r"|TOTAL\s+(?:RATES?\s+)?DUE"                        # generic
    r"|Total\s+balance\s+payable"                         # Knox
    r"|ASSESSMENT\s+TOTAL"                               # Maroondah
    r"|TOTAL\s+BALANCE"                                  # Maroondah
    r"|Balance\s+(?:Due|Outstanding|Owing|Payable)"       # generic
    r"|Outstanding\s+(?:Balance|Amount|Owing)"            # generic
    r"|Amount\s+(?:Now\s+)?(?:Due|Owing|Payable)"        # generic
    r"|Net\s+(?:Amount\s+)?(?:Due|Payable)"              # some councils
    r"|Unpaid\s+(?:Balance|Amount|Rates?)"               # generic
    r"|Overdue\s+Amount"                                  # generic (only if no paid)
    r"|Balance\s+to\s+Pay",                              # generic
    re.IGNORECASE,
)

# Indigo "Total: $905.00" — only used after a payments line is found
_INDIGO_TOTAL_RE = re.compile(
    r"^\s*Total\s*:\s*\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE | re.MULTILINE,
)

# Ballarat "Current Total:2,349.872,349.87" (pypdf duplicates the amount)
# — used as direct annual amount when present
_CURRENT_TOTAL_RE = re.compile(
    r"Current\s+Total\s*:\s*\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Priority subtotal patterns — ordered from most-specific to most-generic.
# Checked in order; first match wins.
#
# 1. "Rates & Charges Sub Total for 2025/26 $1,009.89"  (Bass Coast)
# 2. "Current Years Rates and Charges Sub Total 3,341.70"  (Baw Baw)
# 3. Generic "Sub Total $X" or "Sub total: $X"  (Knox, etc.)
# 4. "Current Total: $X"  (Ballarat)
_SUBTOTAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"Rates\s*&\s*Charges\s+Sub\s+Total(?:\s+for\s+\d{4}/\d{2,4})?\s+\$?\s*([\d,]+\.\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"Current\s+Years?\s+Rates\s+and\s+Charges\s+Sub\s+Total\s+\$?\s*([\d,]+\.\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:[A-Za-z][A-Za-z\s&]*\s+)?Sub\s+[Tt]otal\s*:?\s*\$?\s*([\d,]+\.\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"Current\s+Total\s*:\s*\$?\s*([\d,]+\.\d{2})",
        re.IGNORECASE,
    ),
]

# Keep individual aliases for the two that are also used in _extract_annual_amount notes
_SUB_TOTAL_RE = _SUBTOTAL_PATTERNS[2]

# Any positive dollar amount (with or without $ prefix)
_ANY_DOLLAR_RE = re.compile(r"\$\s*([\d,]+\.\d{2})")
_ANY_PLAIN_AMT_RE = re.compile(r"(?<!\d)([\d,]+\.\d{2})(?!\d)")

# Negative dollar presentations: -$X  −$X  ($X  -X (no dollar)
_NEG_DOLLAR_RE = re.compile(r"[\-−(]\s*\$?\s*([\d,]+\.\d{2})")

_NEGATIVE_LINE_RE = re.compile(
    r"(?im)^\s*"
    r"(?!.*\b(?:arrears|interest|legal|administration|refund|overpayment)s?\b)"
    r"(?=.*\b(?:payment|paid|pension|rebate|concession|credit|discount|waiver|remission|exception)s?\b)"
    r"([^\n]*?)"
    r"(?:\$?\s*[\-−]\s*|\(\$?\s*)"
    r"([\d,]+\.\d{2})\)?\s*$"
)

_FINAL_BALANCE_LINE_RE = re.compile(
    r"(?im)^\s*([^\n]*?"
    r"(?:balance\s+(?:owing|due|outstanding|payable|to\s+pay)"
    r"|net\s+total\s+outstanding"
    r"|total\s+balance(?:\s+outstanding)?"
    r"|assessment\s+total"
    r"|amount\s+(?:due|owing|payable)"
    r"|due\s+payment"
    r"|payment\s+due)"
    r"[^\n$]{0,80})"
    r"\$?\s*([\d,]+\.\d{2})\s*$"
)

_FINAL_BALANCE_LABEL_RE = re.compile(
    r"(?:balance\s+(?:owing|due|outstanding|payable|to\s+pay)"
    r"|net\s+total\s+outstanding"
    r"|total\s+balance(?:\s+outstanding)?"
    r"|assessment\s+total"
    r"|amount\s+(?:due|owing|payable)"
    r"|due\s+payment"
    r"|payment\s+due)",
    re.IGNORECASE,
)

_TABLE_HEADER_RE = re.compile(
    r"RATES\s*&\s*CHARGES\s+LEVIED\s+REBATES\s+BALANCE",
    re.IGNORECASE,
)

_TABLE_ROW_RE = re.compile(
    r"(?im)^\s*"
    r"([A-Za-z][A-Za-z/&,\- ]*[A-Za-z])"
    r"\s+(-?[\d,]+\.\d{2})"
    r"(?:\s+(-?[\d,]+\.\d{2}))?"
    r"(?:\s+(-?[\d,]+\.\d{2}))?\s*$"
)

_NEGATIVE_LABEL_RE = re.compile(
    r"\b(?:payment|paid|pension|rebate|concession|credit|discount|waiver|remission|exception)s?\b",
    re.IGNORECASE,
)

_NEGATIVE_EXCLUDE_LABEL_RE = re.compile(
    r"\b(?:arrears|interest|legal|administration|refund|overpayment)s?\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Generic charge-row detection — used to sum current-year charge lines.
#
# Accounting structure detection: we identify "annual charge rows" by their
# label content, not by council name.  A row is a charge if:
#   - its label contains at least one charge-type keyword (rate, waste, levy …)
#   - its label does NOT contain any exclusion keyword (total, payment, arrears …)
#   - the first dollar amount on the line is positive
#
# This handles Brimbank, Merri-bek, Maroondah, Bass Coast without any
# council-specific special-casing.
# ---------------------------------------------------------------------------

_ANNUAL_CHARGE_LABEL_RE = re.compile(
    r"\b(?:general\s+rate|general|rates?|rate|area|chg|charge|waste|garbage|recycling|"
    r"municipal|levy|esvf|emergency\s+services?|environmental|infrastructure|"
    r"fire\s+services?)\b",
    re.IGNORECASE,
)

# Any of these words in the label means the line is NOT an annual charge row.
_EXCLUDE_ANNUAL_CHARGE_RE = re.compile(
    r"\b(?:arrears|interest|legal|bank|refund|overpayment|balance|outstanding|"
    r"total|sub\s*total|assessment|pension|rebate|credit|concession|discount|"
    r"waiver|payment|paid|adjustment|receipt|potential|notice|order|hazard|"
    r"compulsory|special|other|debt)\b",
    re.IGNORECASE,
)

# Matches: label (2–60 chars, can include spaces/hyphens) then a positive dollar.
# Deliberately excludes leading "-" on the amount so payment lines don't match.
_CHARGE_ROW_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 &/(),.-]{2,60}?)\s+\$?\s*([\d,]+\.\d{2})\b",
    re.MULTILINE,
)

# Line-safe scanner: captures label + Date Levied date + amount on one line.
# Anchored to start/end of line so [^\n$]* can no longer span across newlines
# and accidentally attach to a later dollar amount.
_DATE_LEVIED_SCAN_RE = re.compile(
    r"(?im)^\s*"
    r"(?P<label>[A-Za-z0-9][A-Za-z0-9 &/(),.'\-]{2,120}?)"
    r"[ \t]+Date[ \t]+Levied[ \t]+"
    r"\d{1,2}[/\-]\d{1,2}[/\-]\d{4}"
    r"[ \t]*(?::)?[ \t]*\$?[ \t]*(?P<amount>[\d,]+\.\d{2})"
    r"[ \t]*$"
)

# Lines to EXCLUDE from the Date Levied sum (arrears / adjustments / payments)
_EXCLUDE_DATE_LEVIED_RE = re.compile(
    r"(?:arrears|interest|payment|rebate|adjustment|credit)",
    re.IGNORECASE,
)


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
        r"|total\s+(?:annual\s+)?payable\s+for\s+the\s+year"
        r"|total\s+fees?\s+(?:and\s+charges?\s+)?for\s+the\s+year"
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
    re.compile(
        r"(?:total\s+)?(?:general\s+)?rates?\s+(?:and\s+charges?\s+)?(?:levy|levied)[^\n$]{0,40}(\$[\d,]+\.\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"total\s+amount\s+(?:levied|charged|payable)[^\n$]{0,40}(\$[\d,]+\.\d{2})",
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


def _iter_document_text_sources(doc: Document) -> list[tuple[str, str]]:
    """Return every distinct text source currently available on the Document.

    This is the local version of multi-source evidence gathering. The current
    ingest pipeline stores pypdf text as `raw_text`, a normalized variant as
    `normalized_text`, and may attach additional extracted text in metadata.
    We merge every distinct string we can find and run the extractor over each.
    """
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(label: str, value: object) -> None:
        if not isinstance(value, str):
            return
        text = value.strip()
        if not text:
            return
        fingerprint = _compact(text)
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        candidates.append((label, text))

    _add("raw_text", doc.raw_text)
    _add("normalized_text", doc.normalized_text)

    for idx, page in enumerate(doc.pages, start=1):
        _add(f"page[{idx}].text", page.text)
        _add(f"page[{idx}].normalized_text", page.normalized_text)
        for key in ("pdfplumber_text", "ocr_text", "table_text"):
            _add(f"page[{idx}].metadata.{key}", page.metadata.get(key))

    for key in ("pdfplumber_text", "ocr_text", "table_text", "merged_text"):
        _add(f"metadata.{key}", doc.metadata.get(key))

    return candidates


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
# Paid / outstanding amount helpers (keyword-first, then dollar scan)
# ---------------------------------------------------------------------------


def _scan_for_amount(window: str, keyword_len: int, allow_negative: bool = False) -> str | None:
    """Find the first dollar amount in `window` after the first `keyword_len` chars.

    Tries with-$ first, then bare digits (Ballarat uses no $ on line items).
    Returns raw digit string e.g. "1,208.97", or None.
    """
    after = window[keyword_len:]
    # Try negative presentation first when requested (-$X, −$X, (X)
    if allow_negative:
        neg = _NEG_DOLLAR_RE.search(after)
        if neg:
            return neg.group(1)
    # Positive dollar amount
    pos = _ANY_DOLLAR_RE.search(after)
    if pos:
        return pos.group(1)
    # Bare number (no $ sign — Ballarat format "Less Payments Received-2,349.87")
    if allow_negative:
        neg_bare = re.search(r"[\-−]\s*([\d,]+\.\d{2})", after)
        if neg_bare:
            return neg_bare.group(1)
    bare = _ANY_PLAIN_AMT_RE.search(after)
    if bare:
        return bare.group(1)
    return None


def _find_paid_amount(text: str) -> tuple[str, str] | None:
    """Return (digits, context) for the already-paid amount on any LIC format.

    Searches for the 'Less Payments' keyword (all three council formats),
    then grabs the first dollar/number in the following 300 chars.

    Returns None when no paid-amount keyword is present.
    """
    m = _PAID_KW_RE.search(text)
    if not m:
        return None
    window = text[m.start(): m.end() + 300]
    digits = _scan_for_amount(window, len(m.group(0)), allow_negative=True)
    if digits:
        ctx = _compact(window[: min(len(window), 120)])
        return digits, ctx
    return None


def _find_outstanding_amount(text: str, paid_pos: int = 0) -> tuple[str, str] | None:
    """Return (digits, context) for the balance still owing, all LIC formats.

    Searches the primary outstanding keywords (Brimbank / Ballarat / generic).
    If the matched amount is $0.00 AND a paid amount was found (meaning money
    WAS paid, so the document balance is not zero), we discard the match and
    fall through — this avoids hitting a "$0.00" table-header line (Indigo).

    Falls back to Indigo-style "Total: $X" after the payments line.

    paid_pos: character position where the paid keyword was found.
    """
    m = _OUTSTANDING_KW_RE.search(text)
    if m:
        window = text[m.start(): m.end() + 200]
        digits = _scan_for_amount(window, len(m.group(0)))
        if digits:
            try:
                val = _parse_dollar(digits)
                # Sanity: if outstanding = $0 but we have a paid_pos, check
                # whether there's a more meaningful balance after the paid line.
                if val == 0.0 and paid_pos > 0:
                    # Fall through to Indigo-style check below
                    pass
                else:
                    return digits, _compact(window[:120])
            except ValueError:
                return digits, _compact(window[:120])

    # Indigo fallback: "Total: $905.00" immediately after payment line.
    # Only look AFTER the paid keyword position to avoid header lines.
    if paid_pos > 0:
        after_paid = text[paid_pos:]
        im = _INDIGO_TOTAL_RE.search(after_paid)
        if im:
            return im.group(1), _compact(im.group(0))

    # Re-check the outstanding keyword match even if it was $0.00
    # (Ballarat fully-paid case: outstanding really is $0)
    if m:
        window = text[m.start(): m.end() + 200]
        digits = _scan_for_amount(window, len(m.group(0)))
        if digits:
            return digits, _compact(window[:120])

    return None


def _find_negative_adjustments(text: str) -> list[tuple[float, str]]:
    adjustments: list[tuple[float, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _NEGATIVE_LINE_RE.finditer(text):
        label = _compact(match.group(1))
        digits = match.group(2)
        key = (label.lower(), digits)
        if key in seen:
            continue
        seen.add(key)
        try:
            value = _parse_dollar(digits)
        except ValueError:
            continue
        if value <= 0:
            continue
        adjustments.append((value, _compact(match.group(0))))

    lines = [line.rstrip() for line in text.splitlines()]
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        if not _NEGATIVE_LABEL_RE.search(line) or _NEGATIVE_EXCLUDE_LABEL_RE.search(line):
            continue
        for next_index in range(index + 1, min(index + 3, len(lines))):
            next_line = lines[next_index].strip()
            if not next_line:
                continue
            match = re.fullmatch(r"(?:\$?\s*[\-−]\s*|\(\$?\s*)([\d,]+\.\d{2})\)?", next_line)
            if not match:
                continue
            digits = match.group(1)
            key = (line.lower(), digits)
            if key in seen:
                break
            seen.add(key)
            try:
                value = _parse_dollar(digits)
            except ValueError:
                break
            if value > 0:
                adjustments.append((value, _compact(f"{line} {next_line}")))
            break
    return adjustments


def _find_final_balance_amount(text: str) -> tuple[float, str] | None:
    matches: list[tuple[float, str]] = []
    for match in _FINAL_BALANCE_LINE_RE.finditer(text):
        try:
            value = _parse_dollar(match.group(2))
        except ValueError:
            continue
        if value < 0:
            continue
        matches.append((value, _compact(match.group(0))))
    if not matches:
        lines = [line.rstrip() for line in text.splitlines()]
        for index, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line or not _FINAL_BALANCE_LABEL_RE.search(line):
                continue
            for next_index in range(index + 1, min(index + 4, len(lines))):
                next_line = lines[next_index].strip()
                if not next_line:
                    continue
                amount_match = re.fullmatch(r"\$?\s*([\d,]+\.\d{2})", next_line)
                if not amount_match:
                    break
                try:
                    value = _parse_dollar(amount_match.group(1))
                except ValueError:
                    break
                matches.append((value, _compact(f"{line} {next_line}")))
                break
    if not matches:
        return None
    non_zero = [item for item in matches if item[0] > 0]
    return non_zero[-1] if non_zero else matches[-1]


def _extract_table_totals(text: str) -> tuple[float, float, float, str] | None:
    header = _TABLE_HEADER_RE.search(text)
    if not header:
        return None

    lines = text[header.end():].splitlines()
    levied_total = 0.0
    rebate_total = 0.0
    balance_total = 0.0
    matched_rows: list[str] = []

    stop_tokens = (
        "less payments",
        "assessment total",
        "total balance",
        "potential liabilities",
    )
    excluded_labels = (
        "arrears",
        "interest",
        "legal",
        "administration fee",
        "bank fees",
        "refund",
        "overpayment",
    )

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if matched_rows:
                break
            continue
        lower = line.lower()
        if any(token in lower for token in stop_tokens):
            break
        match = _TABLE_ROW_RE.match(line)
        if not match:
            continue
        label = _compact(match.group(1))
        if any(token in label.lower() for token in excluded_labels):
            continue
        amounts = [group for group in match.groups()[1:] if group is not None]
        if len(amounts) < 2:
            continue
        try:
            levied = _parse_dollar(amounts[0].lstrip("-"))
            rebate = _parse_dollar(amounts[1].lstrip("-")) if len(amounts) >= 2 else 0.0
            balance = _parse_dollar(amounts[2].lstrip("-")) if len(amounts) >= 3 else 0.0
        except ValueError:
            continue
        levied_total += levied
        rebate_total += rebate
        balance_total += balance
        matched_rows.append(_compact(line))

    if not matched_rows or levied_total <= 0:
        return None
    quote = "; ".join(matched_rows[:6])
    return round(levied_total, 2), round(rebate_total, 2), round(balance_total, 2), quote


# ---------------------------------------------------------------------------
# Two-column "Amounts Levied / Outstanding Amount" table (Central Goldfields style)
#
# Layout:
#   Amounts Levied    Outstanding Amount
#   General Area          $833.83     $208.21
#   Garbage Area          $713.05     $178.26
#   Municipal Chg         $202.00      $50.50
#
# The inline row scanner would grab the FIRST dollar amount it sees on each
# line, which is the Levied column — correct.  But when pdfplumber collapses
# two columns onto one line without a clear column separator, the first amount
# matched might actually be the Outstanding column.  This dedicated parser
# anchors on the column-header text and explicitly reads both columns, always
# summing only the Levied (first) column.
# ---------------------------------------------------------------------------

_TWO_COL_LEVIED_OUTSTANDING_RE = re.compile(
    r"Amounts\s+Levied\s+Outstanding\s+Amount(?P<body>.*?)"
    r"(?:TOTAL|ADDITIONAL|Page\s*\d|$)",
    re.IGNORECASE | re.DOTALL,
)

_TWO_AMOUNT_ROW_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z &/.-]{1,60}?)\s+"
    r"\$?(?P<levied>[\d,]+\.\d{2})\s+"
    r"\$?(?P<outstanding>[\d,]+\.\d{2})\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _sum_levied_outstanding_table(text: str) -> tuple[float, str] | None:
    """Parse a two-column Amounts-Levied / Outstanding-Amount table.

    Returns (levied_total, quote) using only the first (Levied) column,
    or None if the header is not present.
    """
    m = _TWO_COL_LEVIED_OUTSTANDING_RE.search(text)
    if not m:
        return None

    body = m.group("body")
    total = 0.0
    rows: list[str] = []

    for r in _TWO_AMOUNT_ROW_RE.finditer(body):
        label = _compact(r.group("label"))
        if _EXCLUDE_ANNUAL_CHARGE_RE.search(label):
            continue
        if not _ANNUAL_CHARGE_LABEL_RE.search(label):
            continue
        try:
            levied = _parse_dollar(r.group("levied"))
        except ValueError:
            continue
        if levied <= 0:
            continue
        total += levied
        rows.append(f"{label} ${levied:,.2f}")

    if not rows:
        return None
    return round(total, 2), "; ".join(rows)


# ---------------------------------------------------------------------------
# Generic LEVIED / BALANCE block parser  (Cardinia and similar layouts).
#
# Handles headers like:
#   "LEVIED  BALANCE"
#   "LEVIED REBATES BALANCE"
#   "AMOUNTS LEVIED  OUTSTANDING AMOUNT"  (already caught by _sum_levied_outstanding_table)
#
# The body may contain:
#   Inline rows:   "GREEN WASTE LEVY  $109.45  $82.09"
#   Stacked rows:  labels first, then pairs of amounts on separate lines
#                  "RATES\nGARBAGE\n$1,215.80 $912.00\n$384.50 $288.37"
#
# We always take the FIRST amount in each pair (leftmost = levied / charged).
# ---------------------------------------------------------------------------

_LEVIED_BALANCE_HEADER_RE = re.compile(
    r"(?:"
    r"LEVIED\s+(?:REBATES?\s+)?BALANCE"
    r"|AMOUNTS?\s+LEVIED\s+OUTSTANDING\s+AMOUNT"   # already in _sum_levied_outstanding_table
    r"|RATES?\s*(?:&|AND)\s*CHARGES?\s+LEVIED\s+(?:REBATES?\s+)?BALANCE"
    r")",
    re.IGNORECASE,
)

# Two amounts side by side on one line — captures levied (first) and balance (second)
_PAIRED_AMOUNTS_RE = re.compile(
    r"\$?([\d,]+\.\d{2})\s+\$?([\d,]+\.\d{2})"
)


def _sum_levied_balance_block(text: str) -> tuple[float, str] | None:
    """Parse a LEVIED/BALANCE two-column block.

    Handles both:
    - Inline rows: "GREEN WASTE LEVY $109.45 $82.09"
    - Stacked rows: label on one line, amounts (levied, balance) on next line
      e.g. "RATES\\n$1,215.80 $912.00"

    Always sums the first (levied) column only.
    """
    header_m = _LEVIED_BALANCE_HEADER_RE.search(text)
    if not header_m:
        return None

    body = text[header_m.end():]
    # Stop at the end of the charges section
    stop_m = re.search(
        r"TOTAL\s+OUTSTANDING|TOTAL\s+AMOUNT\s+DUE|TOTAL\s+BALANCE|"
        r"ADDITIONAL\s+INFORMATION|NOTICES?\s*\n|Page\s+\d",
        body, re.IGNORECASE,
    )
    if stop_m:
        body = body[: stop_m.start()]

    total = 0.0
    rows: list[str] = []
    seen_labels: set[str] = set()
    lines = [_compact(ln) for ln in body.splitlines() if _compact(ln)]

    # Pass 1: inline rows — label + two amounts on the same line
    inline_seen: set[int] = set()
    for i, line in enumerate(lines):
        # Must have a label portion AND two amounts
        pm = _PAIRED_AMOUNTS_RE.search(line)
        if not pm:
            continue
        label_part = line[: pm.start()].strip()
        if not label_part or not re.search(r"[A-Za-z]{3,}", label_part):
            continue
        label = _compact(label_part)
        key = label.lower()
        if key in seen_labels:
            continue
        if _EXCLUDE_ANNUAL_CHARGE_RE.search(label):
            continue
        try:
            levied = _parse_dollar(pm.group(1))
        except ValueError:
            continue
        if levied <= 0:
            continue
        seen_labels.add(key)
        inline_seen.add(i)
        total += levied
        rows.append(f"{label} ${levied:,.2f}")

    # Pass 2: stacked rows — labels on separate lines, followed by paired amount lines
    label_buf: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if i in inline_seen:
            continue
        pm = _PAIRED_AMOUNTS_RE.fullmatch(line)
        if pm:
            # This is a pure amounts line — pair with the most recent buffered label
            if label_buf:
                _, lbl = label_buf.pop(0)
                key = lbl.lower()
                if key not in seen_labels and not _EXCLUDE_ANNUAL_CHARGE_RE.search(lbl):
                    try:
                        levied = _parse_dollar(pm.group(1))
                    except ValueError:
                        continue
                    if levied > 0:
                        seen_labels.add(key)
                        total += levied
                        rows.append(f"{lbl} ${levied:,.2f}")
        elif (
            re.search(r"[A-Za-z]{3,}", line)
            and not _PAIRED_AMOUNTS_RE.search(line)
            and _ANNUAL_CHARGE_LABEL_RE.search(line)
            and not _EXCLUDE_ANNUAL_CHARGE_RE.search(line)
        ):
            label_buf.append((i, line))
        else:
            label_buf.clear()

    if not rows:
        return None
    return round(total, 2), "; ".join(rows)


def _sum_scrambled_levied_balance_block(text: str) -> tuple[float, str] | None:
    """Handle Cardinia-style extracted text with interleaved LEVIED/BALANCE headers."""
    if not re.search(r"\bLEVIED\b", text, re.IGNORECASE):
        return None
    if not re.search(r"\bBALANCE\b", text, re.IGNORECASE):
        return None
    if not re.search(r"\bTOTAL\s+OUTSTANDING\b", text, re.IGNORECASE):
        return None

    block = re.split(r"TOTAL\s+OUTSTANDING", text, maxsplit=1, flags=re.IGNORECASE)[0]
    lines = [_compact(line) for line in block.splitlines() if _compact(line)]

    total = 0.0
    rows: list[str] = []
    used_line_indexes: set[int] = set()

    for i, line in enumerate(lines):
        m = _PAIRED_AMOUNTS_RE.search(line)
        if not m:
            continue

        label = _compact(line[:m.start()])
        if not label:
            continue
        if _EXCLUDE_ANNUAL_CHARGE_RE.search(label):
            continue
        if not _ANNUAL_CHARGE_LABEL_RE.search(label):
            continue

        levied = _parse_dollar(m.group(1))
        if levied <= 0:
            continue

        total += levied
        rows.append(f"{label} ${levied:,.2f}")
        used_line_indexes.add(i)

    label_queue: list[str] = []

    for i, line in enumerate(lines):
        if i in used_line_indexes:
            continue

        pair = _PAIRED_AMOUNTS_RE.fullmatch(line)
        if pair:
            if label_queue:
                label = label_queue.pop(0)
                levied = _parse_dollar(pair.group(1))
                if levied > 0:
                    total += levied
                    rows.append(f"{label} ${levied:,.2f}")
            continue

        if (
            _ANNUAL_CHARGE_LABEL_RE.search(line)
            and not _EXCLUDE_ANNUAL_CHARGE_RE.search(line)
            and line.upper() not in {"LEVIED", "BALANCE"}
        ):
            label_queue.append(line)

    if not rows:
        return None

    return round(total, 2), "; ".join(rows)


def _sum_current_charge_rows(text: str) -> tuple[float, str] | None:
    """Sum lines that look like annual charge rows by label semantics.

    Scans every line for the pattern "label  $amount".  A line is included if:
      - label contains a charge-type keyword (rate, waste, levy, esvf …)
      - label does NOT contain an exclusion keyword (total, payment, arrears …)
      - the first dollar amount on the line is positive

    Returns (total, quote) or None if nothing matched.
    """
    total = 0.0
    rows: list[str] = []
    seen: set[str] = set()

    for m in _CHARGE_ROW_LINE_RE.finditer(text):
        label = _compact(m.group(1))
        key = label.lower()
        if key in seen:
            continue
        if _EXCLUDE_ANNUAL_CHARGE_RE.search(label):
            continue
        if not _ANNUAL_CHARGE_LABEL_RE.search(label):
            continue
        try:
            val = _parse_dollar(m.group(2))
        except ValueError:
            continue
        if val <= 0:
            continue
        seen.add(key)
        total += val
        rows.append(f"{label} ${m.group(2)}")

    if total > 0 and rows:
        return round(total, 2), "; ".join(rows[:6])
    return None


# "TOTAL CHARGES $1,807.40" — an explicit charge total line on the document.
# Higher confidence than row-summing because the council computed it directly.
_TOTAL_CHARGES_RE = re.compile(
    r"\bTOTAL\s+CHARGES\b\s*:?\s*\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)


def _find_total_charges(text: str) -> tuple[float, str] | None:
    m = _TOTAL_CHARGES_RE.search(text)
    if not m:
        return None
    try:
        val = _parse_dollar(m.group(1))
    except ValueError:
        return None
    if val <= 0:
        return None
    return val, _compact(m.group(0))


_ZERO_ADJUSTMENT_PATTERNS = [
    re.compile(r"Rate\s+Arrears\s*:?\s*\$?\s*0\.00", re.IGNORECASE),
    re.compile(r"Interest\b[^\n]{0,60}:\s*\$?\s*0\.00", re.IGNORECASE),
    re.compile(r"Less\s+Pensioner\s+Rebates?\s*:?\s*\$?\s*0\.00", re.IGNORECASE),
    re.compile(r"Less\s+Payments?\s*:?\s*\$?\s*0\.00", re.IGNORECASE),
]

_TOTAL_DUE_RE = re.compile(
    r"\bTotal\s+Due\s*:?\s*\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)


def _find_zero_adjustment_total_due(text: str) -> tuple[float, str] | None:
    """Return (amount, quote) when all adjustment lines are $0 and Total Due is present.

    Alpine Shire layout: Rate Arrears $0.00, Interest $0.00, Less Pensioner Rebates $0.00,
    Less Payments $0.00 → Total Due equals the sum of current charges directly.
    Requires at least 2 of the 4 zero-adjustment patterns to match (avoids false positives
    on documents that simply don't print those lines at all).
    """
    matched_zero_patterns = sum(1 for p in _ZERO_ADJUSTMENT_PATTERNS if p.search(text))
    if matched_zero_patterns < 2:
        return None
    m = _TOTAL_DUE_RE.search(text)
    if not m:
        return None
    try:
        val = _parse_dollar(m.group(1))
    except ValueError:
        return None
    if val <= 0:
        return None
    return val, _compact(m.group(0))


# ---------------------------------------------------------------------------
# Alpine / "Current Rates and Charges:" block parser
#
# Layout:
#   Current Rates and Charges:
#   General Date Levied 01/07/2025 : $712.72
#   Waste Management Date Levied 01/07/2025 : $0.00
#   Recycling Date Levied 01/07/2025 : $0.00
#   ESVF Date Levied 01/07/2025 : $188.25
#
# Strategy: isolate the block, run the Date Levied scanner on it, sum all
# non-excluded positive amounts, optionally verify against Total Due.
# ---------------------------------------------------------------------------

_CURRENT_RATES_BLOCK_RE = re.compile(
    r"Current\s+Rates\s+and\s+Charges\s*:?\s*(?P<body>.*?)"
    r"(?:Other\s+Rates\s+and\s+Charges\s*:|Potential\s+Liabilities|"
    r"BPAY|Total\s+Due\s*:|Rate\s+Arrears|$)",
    re.IGNORECASE | re.DOTALL,
)

# Label keywords accepted inside a "Current Rates and Charges:" block.
# Broader than _ANNUAL_CHARGE_LABEL_RE — "General" alone is valid here.
_CURRENT_BLOCK_LABEL_RE = re.compile(
    r"\b(?:general|rate|rates|waste|recycling|garbage|municipal|levy|esvf|"
    r"emergency\s+services?|environmental|infrastructure|fire\s+services?|"
    r"area|chg|charge)\b",
    re.IGNORECASE,
)


def _sum_current_rates_date_levied_block(text: str) -> tuple[float, str] | None:
    """Sum "Date Levied" rows inside a "Current Rates and Charges:" block.

    Specifically handles Alpine Shire and similar councils where:
    - Charges are listed as "LABEL  Date Levied DD/MM/YYYY  :  $AMOUNT"
    - The block opens with "Current Rates and Charges:"
    - "General" alone is a valid label (not just "General Rate")

    Returns (total, quote) or None when the block is absent or < 2 rows matched.
    Total Due verification is appended to the quote when it confirms the sum.
    """
    m = _CURRENT_RATES_BLOCK_RE.search(text)
    if not m:
        return None

    body = m.group("body")
    total = 0.0
    rows: list[str] = []
    matched_rows = 0

    for sm in _DATE_LEVIED_SCAN_RE.finditer(body):
        label = _compact(sm.group("label"))
        if _EXCLUDE_DATE_LEVIED_RE.search(label):
            continue
        if _EXCLUDE_ANNUAL_CHARGE_RE.search(label):
            continue
        if not _CURRENT_BLOCK_LABEL_RE.search(label):
            continue
        matched_rows += 1
        try:
            amount = _parse_dollar(sm.group("amount"))
        except ValueError:
            continue
        if amount <= 0:
            continue
        total += amount
        rows.append(f"{label} ${amount:,.2f}")

    if matched_rows < 2 or total <= 0:
        return None

    # Verify against Total Due when zero adjustments apply
    due_m = _TOTAL_DUE_RE.search(text)
    if due_m:
        try:
            due_val = _parse_dollar(due_m.group(1))
            if abs(due_val - total) <= 1.0:
                rows.append(f"verified by Total Due ${due_val:,.2f}")
        except ValueError:
            pass

    return round(total, 2), "; ".join(rows)


# Anchors that open / close a "Charges:" block in stacked-layout PDFs (Monash-style).
# The block starts at "Charges:" and ends before the next accounting section.
_STACKED_BLOCK_RE = re.compile(
    r"Charges\s*:\s*(.*?)\s*"
    r"(?:Pension\s+Rebates?|Additional\s+information|BALANCE\s+OWING|"
    r"Total\s+Balance|Less\s+Payments?|Receipts\s+and|Net\s+Amount)",
    re.IGNORECASE | re.DOTALL,
)

# A line that is purely a dollar amount (with optional leading $).
_AMOUNT_ONLY_LINE_RE = re.compile(r"^\$?\s*([\d,]+\.\d{2})$")

_CURRENT_LEVIED_MATRIX_ROW_RE = re.compile(
    r"(?is)"
    r"(current\s+rates?\s+levied(?:\s+\d{4}/\d{4})?)"
    r"(?P<body>.{0,250}?)"
    r"(?:current\s+interest|rebates?|payments?|balance\s+outstanding|summary\s+of|$)"
)

# "Current Rates Levied $665.24" — Wyndham-style explicit annual subtotal line.
# This is the council's own computed total of all current-year rates and charges.
_CURRENT_RATES_LEVIED_LINE_RE = re.compile(
    r"\bCurrent\s+Rates?\s+Levied\b[^\n$]{0,40}\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)

_ADJUSTMENT_ROW_RE = re.compile(r"\badjustments?\b", re.IGNORECASE)

_AMOUNT_ANY_RE = re.compile(r"\(?\$?\s*-?\s*([\d,]+\.\d{2})\)?")


def _sum_stacked_charge_block(text: str) -> tuple[float, str] | None:
    """Handle stacked-layout PDFs where labels and amounts appear on separate lines.

    Monash-style layout::

        Charges:
        Residential/Supplementary Rate
        Recycle and Waste Charge
        Emergency Services and Volunteers Fund - State Gov
        Residential Waste
        1,839.75
        65.00
        355.70
        246.30

    Labels come first (N lines), then N matching amounts follow in the same order.
    We collect labels that pass the charge/exclude filter, then pair them with the
    first N amounts.  Safety: only proceed when len(amounts) >= len(labels).
    """
    m = _STACKED_BLOCK_RE.search(text)
    if not m:
        return None

    block = m.group(1)
    lines = [_compact(ln) for ln in block.splitlines() if _compact(ln)]

    labels: list[str] = []
    amounts: list[float] = []

    for line in lines:
        am = _AMOUNT_ONLY_LINE_RE.match(line)
        if am:
            try:
                amounts.append(_parse_dollar(am.group(1)))
            except ValueError:
                pass
        else:
            if (
                _ANNUAL_CHARGE_LABEL_RE.search(line)
                and not _EXCLUDE_ANNUAL_CHARGE_RE.search(line)
            ):
                labels.append(line)

    if not labels or not amounts or len(amounts) < len(labels):
        return None

    used = amounts[: len(labels)]
    positive = [(lbl, amt) for lbl, amt in zip(labels, used) if amt > 0]
    if not positive:
        return None

    total = round(sum(amt for _, amt in positive), 2)
    quote = "; ".join(f"{lbl} ${amt:,.2f}" for lbl, amt in positive)
    return total, quote


def _sum_current_levied_matrix_row(text: str) -> tuple[float, str] | None:
    """Handle matrix tables where the last amount on the levied row is the total."""
    m = _CURRENT_LEVIED_MATRIX_ROW_RE.search(text)
    if not m:
        return None

    row_text = _compact(m.group(0))
    amounts = [_parse_dollar(x) for x in _AMOUNT_ANY_RE.findall(row_text)]
    if not amounts:
        return None

    total = round(amounts[-1], 2)
    if total <= 0:
        return None

    return total, row_text


def _find_current_rates_levied(text: str) -> tuple[float, str] | None:
    """Return (amount, quote) for an explicit 'Current Rates Levied $X' line.

    Wyndham layout:
        General VRL Rates       $454.28
        Municipal Charge        $ 64.35
        Fire Services Levy      $146.61
        Current Rates Levied    $665.24   ← council's own annual subtotal

    This line is the council's own pre-computed total of current charges.
    It must be preferred over any individual charge row or adjustment row.
    """
    m = _CURRENT_RATES_LEVIED_LINE_RE.search(text)
    if not m:
        return None
    try:
        val = _parse_dollar(m.group(1))
    except ValueError:
        return None
    if val <= 0:
        return None
    return val, _compact(m.group(0))


_HEADER_SPLIT_RE = re.compile(r"\s{2,}")
_HEADER_PHRASE_RE = re.compile(
    r"General\s+Rates?|ESVF|Garbage\s+Charge|Waste(?:\s+Charge)?|"
    r"Municipal\s+Charge|Amounts?\s+Levied|Outstanding\s+Amount|"
    r"Levied|Rebates?|Balance|Total",
    re.IGNORECASE,
)
_ANNUAL_COLUMN_RE = re.compile(
    r"\b(?:levied|current|charges?|rates?|general\s+rates?|esvf|garbage|waste|municipal)\b",
    re.IGNORECASE,
)
_BALANCE_COLUMN_RE = re.compile(
    r"\b(?:balance|outstanding|owing|due|payable)\b",
    re.IGNORECASE,
)
_TOTAL_COLUMN_RE = re.compile(r"\btotal\b", re.IGNORECASE)
_DISCOUNT_COLUMN_RE = re.compile(
    r"\b(?:rebate|credit|concession|discount|payment|paid)\b",
    re.IGNORECASE,
)
_PAYMENT_ROW_RE = re.compile(
    r"\b(?:payments?|paid|credit|rebate|concession|discount|waiver|remission)\b",
    re.IGNORECASE,
)
_BALANCE_ROW_RE = re.compile(
    r"\b(?:balance|outstanding|owing|due|payable|arrears)\b",
    re.IGNORECASE,
)
_ANNUAL_TOTAL_ROW_RE = re.compile(
    r"\b(?:current\s+rates?\s+levied|current\s+levied|annual\s+rates?\s+and\s+charges|"
    r"rates?\s+and\s+charges\s+sub\s+total|current\s+years?\s+rates?\s+and\s+charges\s+sub\s+total)\b",
    re.IGNORECASE,
)


def _split_header_tokens(line: str) -> list[str]:
    tokens = [_compact(part) for part in _HEADER_SPLIT_RE.split(line.strip()) if _compact(part)]
    if len(tokens) >= 2:
        return tokens
    phrase_tokens = [_compact(m.group(0)) for m in _HEADER_PHRASE_RE.finditer(line)]
    if phrase_tokens:
        return phrase_tokens
    return []


def _looks_like_header_line(line: str) -> bool:
    if _AMOUNT_ANY_RE.search(line):
        return False
    tokens = _split_header_tokens(line)
    if len(tokens) >= 2:
        return True
    lowered = line.lower()
    return (
        "levied" in lowered
        or "outstanding amount" in lowered
        or "balance" in lowered
        or "rebates" in lowered
        or " total " in f" {lowered} "
    )


def _infer_column_names(header_lines: list[str], amount_count: int) -> list[str]:
    if amount_count <= 0:
        return []

    for line in reversed(header_lines[-3:]):
        tokens = _split_header_tokens(line)
        if len(tokens) == amount_count:
            return tokens
        if len(tokens) > amount_count:
            return tokens[-amount_count:]

    combined = " ".join(header_lines[-3:])
    lowered = combined.lower()
    if amount_count == 2 and "levied" in lowered and "balance" in lowered:
        return ["Levied", "Balance"]
    if amount_count == 2 and "levied" in lowered and "outstanding" in lowered:
        return ["Amounts Levied", "Outstanding Amount"]
    if amount_count == 3 and "levied" in lowered and "rebate" in lowered and "balance" in lowered:
        return ["Levied", "Rebates", "Balance"]

    return [f"col_{i + 1}" for i in range(amount_count)]


def _classify_column_name(name: str) -> str:
    if _BALANCE_COLUMN_RE.search(name):
        return "balance"
    if _DISCOUNT_COLUMN_RE.search(name):
        return "discount"
    if _TOTAL_COLUMN_RE.search(name):
        return "total"
    if _ANNUAL_COLUMN_RE.search(name):
        return "annual"
    return "unknown"


def _classify_row_label(label: str) -> str:
    if _ANNUAL_TOTAL_ROW_RE.search(label):
        return "annual_total"
    if _PAYMENT_ROW_RE.search(label):
        return "payment"
    if _ADJUSTMENT_ROW_RE.search(label):
        return "payment"  # adjustments are post-levy corrections, never annual charges
    if _BALANCE_ROW_RE.search(label):
        return "balance"
    if _ANNUAL_CHARGE_LABEL_RE.search(label) and not _EXCLUDE_ANNUAL_CHARGE_RE.search(label):
        return "charge"
    return "unknown"


def _normalize_money_rows(text: str, *, section_name: str = "rates") -> list[MoneyRow]:
    rows: list[MoneyRow] = []
    header_lines: list[str] = []
    pending_label_lines: list[str] = []

    for raw_line in text.splitlines():
        line = _compact(raw_line)
        if not line:
            continue

        amount_matches = list(_AMOUNT_ANY_RE.finditer(line))
        if not amount_matches:
            if _looks_like_header_line(raw_line):
                header_lines.append(line)
                header_lines = header_lines[-5:]
            elif re.search(r"[A-Za-z]{3,}", line):
                pending_label_lines.append(line)
                pending_label_lines = pending_label_lines[-3:]
            continue

        label = _compact(line[: amount_matches[0].start()])
        if pending_label_lines:
            label_parts = pending_label_lines[:]
            if label:
                label_parts.append(label)
            label = _compact(" ".join(label_parts))
        pending_label_lines = []

        if not label:
            continue

        amounts: list[float] = []
        for match in amount_matches:
            try:
                amounts.append(_parse_dollar(match.group(1)))
            except ValueError:
                continue
        if not amounts:
            continue

        rows.append(
            MoneyRow(
                label=label,
                amounts=amounts,
                column_names=_infer_column_names(header_lines, len(amounts)),
                section=section_name,
                source_text=line,
            )
        )

    return rows


def _sum_moneyrow_annual_total(rows: list[MoneyRow]) -> tuple[float, str] | None:
    for row in rows:
        if _classify_row_label(row.label) != "annual_total":
            continue
        if not row.amounts:
            continue

        total_idx = -1
        for idx, name in enumerate(row.column_names):
            if _classify_column_name(name) == "total":
                total_idx = idx
                break
        value = row.amounts[total_idx] if 0 <= total_idx < len(row.amounts) else row.amounts[-1]
        if value > 0:
            return round(value, 2), row.source_text
    return None


def _sum_moneyrow_charge_columns(rows: list[MoneyRow]) -> tuple[float, str] | None:
    total = 0.0
    evidence: list[str] = []

    for row in rows:
        if _classify_row_label(row.label) != "charge":
            continue
        if not row.amounts:
            continue

        chosen_index: int | None = None
        for idx, name in enumerate(row.column_names[: len(row.amounts)]):
            if _classify_column_name(name) == "annual":
                chosen_index = idx
                break

        if chosen_index is None and len(row.amounts) == 1:
            chosen_index = 0

        if chosen_index is None:
            continue

        value = row.amounts[chosen_index]
        if value <= 0:
            continue

        total += value
        evidence.append(f"{row.label} ${value:,.2f}")

    if total > 0 and evidence:
        return round(total, 2), "; ".join(evidence[:6])
    return None


# ---------------------------------------------------------------------------
# Section isolator — restrict extraction to the rates/charges section only.
# Prevents payment due dates, notice text, and property info from poisoning
# the annual amount extraction.
# ---------------------------------------------------------------------------

_RATES_SECTION_START_RE = re.compile(
    r"(?:"
    r"RATES?\s*(?:AND\s*)?CHARGES?\s+(?:DETAILS?|SCHEDULE|FOR|LEVIED|SUMMARY)"
    r"|PROPERTY\s+RATES?\s*(?:AND\s*CHARGES?)?"
    r"|MUNICIPAL\s+RATES?\s+(?:NOTICE|ASSESSMENT|DETAILS?)"
    r"|RATES?\s+(?:AND\s+CHARGES?\s+)?DETAILS?"
    r"|CHARGES?\s+SCHEDULE"
    r"|FINANCIAL\s+INFORMATION"
    r")",
    re.IGNORECASE,
)

_RATES_SECTION_END_RE = re.compile(
    r"(?:"
    r"NOTICES?\s*\n"
    r"|POTENTIAL\s+LIABILIT"
    r"|HOW\s+TO\s+PAY"
    r"|PAYMENT\s+OPTIONS?"
    r"|BPAY\s+(?:BILLER|CODE|REF)"
    r"|ADDITIONAL\s+INFORMATION"
    r"|SUPPLEMENTARY\s+INFORMATION"
    r"|INSTALMENT\s+OPTION"
    r"|PROPERTY\s+INFORMATION\s*\n"
    r")",
    re.IGNORECASE,
)


def _isolate_rates_section(text: str) -> str:
    """Return only the rates/charges section of the document, if detectable.

    Falls back to the full text if no section header is found — ensures we
    never return empty text.
    """
    m_start = _RATES_SECTION_START_RE.search(text)
    if not m_start:
        return text
    section = text[m_start.start():]
    m_end = _RATES_SECTION_END_RE.search(section, 200)  # skip at least 200 chars in
    if m_end:
        section = section[: m_end.start()]
    return section or text


# ---------------------------------------------------------------------------
# Balance-value extractor — amounts that appear next to outstanding/due labels
# should NEVER be used as the annual amount.
# ---------------------------------------------------------------------------

_BALANCE_CONTEXT_RE = re.compile(
    r"(?:"
    r"balance\s+(?:owing|due|outstanding|payable|to\s+pay)"
    r"|total\s+outstanding(?:\s+for)?"
    r"|summary\s+of\s+charges\s+outstanding"
    r"|general\s+rates,\s*charges\s*&\s*esvf"
    r"|net\s+total\s+outstanding"
    r"|total\s+balance(?:\s+outstanding)?"
    r"|assessment\s+total"
    r"|amount\s+(?:due|owing|payable)"
    r"|total\s+(?:amount\s+)?(?:due|payable)\s+(?:now|today)?"
    r"|overdue\s+(?:amount|balance)"
    r")"
    r"[^\n$]{0,120}\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)


def _collect_balance_values(text: str) -> set[float]:
    """Return every dollar amount that appears next to a 'balance due' keyword."""
    vals: set[float] = set()
    for m in _BALANCE_CONTEXT_RE.finditer(text):
        try:
            vals.add(round(_parse_dollar(m.group(1)), 2))
        except ValueError:
            pass
    return vals


# ---------------------------------------------------------------------------
# Candidate: one possible annual amount with its source and confidence.
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    value: float
    source: str
    confidence: float
    quote: str
    from_balance: bool = False   # True if detected as a balance value


@dataclass
class MoneyRow:
    label: str
    amounts: list[float]
    column_names: list[str]
    section: str
    source_text: str


_BALANCE_IMMUNE_SOURCES = frozenset({
    "explicit_subtotal[0]", "explicit_subtotal[1]", "explicit_subtotal[2]", "explicit_subtotal[3]",
    "total_charges_line", "levied_outstanding_table", "levied_balance_block",
    "current_rates_date_levied_block", "current_rates_levied_line",
    "zero_adjustments_total_due",
    "scrambled_levied_balance_block", "stacked_charge_block", "current_levied_matrix_row",
    "moneyrow_annual_total_row", "moneyrow_charge_rows",
    "date_levied_verified_by_paid_due",
})


def _mark_balance_candidates(candidates: list[_Candidate], balance_vals: set[float]) -> None:
    """Flag candidates whose value matches a known balance/outstanding value.

    Explicit charges sources (subtotal lines, TOTAL CHARGES) are immune —
    when all rates are unpaid, the charge total equals the balance due, but
    the charge total is still the correct annual amount.
    """
    for c in candidates:
        if c.source in _BALANCE_IMMUNE_SOURCES:
            continue
        if round(c.value, 2) in balance_vals:
            c.from_balance = True


def _collect_all_candidates(text: str, section: str) -> list[_Candidate]:
    """Run every extraction strategy and return ALL non-empty candidates.

    Does NOT return early — all strategies are run so we can cross-validate.
    Uses `section` (isolated rates section) for strategies prone to picking up
    noise from other document sections, and `text` (full) for global patterns.
    """
    candidates: list[_Candidate] = []

    def _add(value: float, source: str, confidence: float, quote: str) -> None:
        if value > 0:
            candidates.append(_Candidate(round(value, 2), source, confidence, quote))

    money_rows = _normalize_money_rows(section)

    # 1. Explicit subtotal patterns
    for i, pat in enumerate(_SUBTOTAL_PATTERNS):
        m = pat.search(section)
        if m:
            try:
                v = _parse_dollar(m.group(1))
                _add(v, f"explicit_subtotal[{i}]", 0.97, _compact(m.group(0)))
            except ValueError:
                pass

    # 1b. TOTAL CHARGES explicit line
    tc = _find_total_charges(section)
    if tc:
        _add(tc[0], "total_charges_line", 0.98, tc[1])

    # 1c. Two-column Amounts Levied / Outstanding table (Central Goldfields)
    lo = _sum_levied_outstanding_table(section)
    if lo:
        _add(lo[0], "levied_outstanding_table", 0.97, lo[1])

    # 1d. Generic LEVIED/BALANCE block (Cardinia and similar — stacked+inline mix)
    scrambled = _sum_scrambled_levied_balance_block(section)
    if scrambled:
        _add(scrambled[0], "scrambled_levied_balance_block", 0.98, scrambled[1])

    # 1e. Generic LEVIED/BALANCE block with contiguous header
    lb = _sum_levied_balance_block(section)
    if lb:
        _add(lb[0], "levied_balance_block", 0.97, lb[1])

    # 2a. Row-normalized current levied/annual total row.
    moneyrow_total = _sum_moneyrow_annual_total(money_rows)
    if moneyrow_total:
        _add(moneyrow_total[0], "moneyrow_annual_total_row", 0.96, moneyrow_total[1])

    # 2b. Stacked charge block (Monash style)
    stacked = _sum_stacked_charge_block(section)
    if stacked:
        _add(stacked[0], "stacked_charge_block", 0.96, stacked[1])

    # 2c. Matrix-style levied row with a final Total column (Glen Eira style)
    matrix = _sum_current_levied_matrix_row(section)
    if matrix:
        _add(matrix[0], "current_levied_matrix_row", 0.98, matrix[1])

    # 2d. Row-normalized charge rows by classified annual columns.
    moneyrow_sum = _sum_moneyrow_charge_columns(money_rows)
    if moneyrow_sum:
        _add(moneyrow_sum[0], "moneyrow_charge_rows", 0.96, moneyrow_sum[1])

    # 2e-pre. "Current Rates and Charges:" block — Date Levied rows (Alpine style)
    # Rank 11 / conf 0.99 — highest priority structural source.
    # Requires ≥2 matched rows so a single stray line can never win.
    crdb = _sum_current_rates_date_levied_block(section)
    if crdb:
        _add(crdb[0], "current_rates_date_levied_block", 0.99, crdb[1])

    # 2e-pre2. Explicit "Current Rates Levied $X" line (Wyndham style)
    # Rank 11 / conf 0.99 — council's own precomputed annual subtotal.
    crl = _find_current_rates_levied(text)
    if crl:
        _add(crl[0], "current_rates_levied_line", 0.99, crl[1])

    # 2e. Inline charge rows
    inline = _sum_current_charge_rows(section)
    if inline:
        _add(inline[0], "inline_charge_rows", 0.95, inline[1])

    # 3. LEVIED column in three-column table
    tt = _extract_table_totals(section)
    if tt:
        _add(
            tt[0],
            "levied_column_table",
            0.95,
            f"{tt[3]}; rebates {_fmt(tt[1])}; balance {_fmt(tt[2])}",
        )

    # 4. Paid + outstanding formula (operates on full text — keywords spread across doc)
    paid_result = _find_paid_amount(text)
    outstanding_result = None
    if paid_result:
        m_paid = _PAID_KW_RE.search(text)
        paid_pos = m_paid.start() if m_paid else 0
        outstanding_result = _find_outstanding_amount(text, paid_pos=paid_pos)
    if paid_result and outstanding_result:
        try:
            paid_val = _parse_dollar(paid_result[0])
            out_val = _parse_dollar(outstanding_result[0])
            _add(paid_val + out_val, "paid_plus_outstanding", 0.95, f"{paid_result[1]}; {outstanding_result[1]}")
        except ValueError:
            pass

    # Also try adjustments + balance formula
    adjustments = _find_negative_adjustments(section)
    final_balance = _find_final_balance_amount(section)
    if adjustments and final_balance:
        adj_total = sum(v for v, _ in adjustments)
        try:
            _add(adj_total + final_balance[0], "adjustments_plus_balance", 0.93,
                 "; ".join(ctx for _, ctx in adjustments) + f"; {final_balance[1]}")
        except TypeError:
            pass

    # 5. Annual Council Rates label
    m = _ANNUAL_COUNCIL_RATES_RE.search(section)
    if m:
        try:
            _add(_parse_dollar(m.group(1)), "annual_council_rates_label", 0.88, _compact(m.group(0)))
        except ValueError:
            pass

    # 5b. Generic annual labels
    for j, pattern in enumerate(_ANNUAL_LABELS):
        m = pattern.search(section)
        if m:
            try:
                _add(_parse_dollar(m.group(1)), f"annual_label[{j}]", 0.85, _compact(m.group(0)))
            except ValueError:
                pass

    # 6. Date Levied scan — line-safe, label-filtered
    scan_matches = list(_DATE_LEVIED_SCAN_RE.finditer(section))
    if scan_matches:
        total = 0.0
        items: list[str] = []
        matched_rows = 0
        for sm in scan_matches:
            label = _compact(sm.group("label"))
            if _EXCLUDE_DATE_LEVIED_RE.search(label):
                continue
            if _EXCLUDE_ANNUAL_CHARGE_RE.search(label):
                continue
            if not _ANNUAL_CHARGE_LABEL_RE.search(label):
                continue
            matched_rows += 1
            try:
                amount = _parse_dollar(sm.group("amount"))
            except ValueError:
                continue
            if amount <= 0:
                continue
            total += amount
            items.append(f"{label} ${amount:,.2f}")
        if total > 0 and matched_rows >= 2:
            _add(round(total, 2), "date_levied_scan", 0.94,
                 "; ".join(items))

    # 6b. Zero-adjustments Total Due — when payments/rebates/arrears/interest are all
    # $0.00 the "Total Due" line equals the annual current charges directly.
    ztd = _find_zero_adjustment_total_due(section)
    if ztd:
        _add(ztd[0], "zero_adjustments_total_due", 0.97, ztd[1])

    # 7. Instalment annualisation (lowest priority — marks needs_review)
    for pat, mult, conf, label in [
        (_QUARTERLY_LABELS, 4, 0.82, "quarterly_x4"),
        (_HALFYEARLY_LABELS, 2, 0.80, "halfyearly_x2"),
        (_MONTHLY_LABELS, 12, 0.78, "monthly_x12"),
    ]:
        m = pat.search(section)
        if m:
            try:
                actual_mult = _period_multiplier_from_range(section) or mult
                _add(round(_parse_dollar(m.group(1)) * actual_mult, 2),
                     label, conf, _compact(m.group(0)))
            except ValueError:
                pass

    # 8. Post-collection guard: downgrade single-item inline_charge_rows when the
    #    value is suspiciously low.  A partial row capture (one charge line from a
    #    multi-line table) should never beat a verified total or a multi-row sum.
    for c in candidates:
        if c.source == "inline_charge_rows":
            item_count = c.quote.count(";") + 1 if c.quote else 1
            if item_count == 1 and c.value < 500.0:
                c.confidence = min(c.confidence, 0.50)

    return candidates


def _collect_document_candidates(doc: Document) -> tuple[list[_Candidate], str, str, list[MoneyRow]]:
    """Run candidate collection across every available text source on the document."""
    all_candidates: list[_Candidate] = []
    all_rows: list[MoneyRow] = []
    seen: set[tuple[str, float, str]] = set()
    primary_text = _doc_text(doc)
    primary_section = _isolate_rates_section(primary_text)

    for _, text in _iter_document_text_sources(doc):
        section = _isolate_rates_section(text)
        all_rows.extend(_normalize_money_rows(section))
        for candidate in _collect_all_candidates(text, section):
            key = (candidate.source, round(candidate.value, 2), _compact(candidate.quote))
            if key in seen:
                continue
            seen.add(key)
            all_candidates.append(candidate)

    return all_candidates, primary_text, primary_section, all_rows


def _priority_score(c: _Candidate) -> float:
    """Score for ranking candidates: higher = better evidence."""
    _SOURCE_RANK = {
        "explicit_subtotal[0]": 10,
        "explicit_subtotal[1]": 10,
        "explicit_subtotal[2]": 9,
        "explicit_subtotal[3]": 9,
        "total_charges_line": 10,
        "levied_outstanding_table": 9,
        "scrambled_levied_balance_block": 10,
        "levied_balance_block": 9,
        "moneyrow_annual_total_row": 10,
        "current_levied_matrix_row": 10,
        "moneyrow_charge_rows": 8,
        "stacked_charge_block": 8,
        "inline_charge_rows": 7,
        "levied_column_table": 7,
        "paid_plus_outstanding": 6,
        "adjustments_plus_balance": 6,
        "annual_council_rates_label": 5,
        "date_levied_scan": 9,
        "zero_adjustments_total_due": 10,
        "current_rates_date_levied_block": 11,
        "current_rates_levied_line": 11,
        "date_levied_verified_by_paid_due": 11,
        "omni_structured_rows": 8,
    }
    base = _SOURCE_RANK.get(c.source, 3) * 0.1 + c.confidence
    if c.from_balance:
        base -= 5.0  # heavy penalty
    return base


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------


def _extract_council_name(doc: Document, text: str, filename: str) -> list[Fact]:
    """Try multiple patterns to extract the council authority name."""

    # 1. "X City/Shire Council" in text (Title Case or ALL CAPS)
    m = _COUNCIL_SUFFIX_RE.search(text) or _COUNCIL_SUFFIX_CAPS_RE.search(text)
    if m:
        name = _compact(m.group(0))
        if name == name.upper():
            name = name.title()
        # Strip department-name prefixes (e.g. "Revenue Services Maroondah City Council"
        # → "Maroondah City Council")
        name = _COUNCIL_DEPT_PREFIX_RE.sub("", name).strip()
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
        raw_domain_name = m.group(1)
        domain_name = raw_domain_name.title()
        # Avoid generic domain names
        if raw_domain_name.lower() not in {"vic", "gov", "au", "landata", "sro"}:
            suffix = _COUNCIL_DOMAIN_SUFFIX.get(raw_domain_name.lower(), "Council")
            name = f"{domain_name} {suffix}"
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_AUTHORITY, name,
                    f"council domain: {m.group(0)}",
                    confidence=0.90,
                    notes="council name inferred from website/email domain",
                )
            ]

    # 4. Filename-derived council name from enquiry title
    m = _FILENAME_COUNCIL_RE.search(filename.replace("_", " "))
    if m:
        raw_name = _compact(m.group(1))
        if raw_name:
            lower_name = raw_name.lower()
            if lower_name.endswith(("city", "shire", "borough", "town")):
                name = f"{raw_name} Council"
            elif "baw baw" in lower_name:
                name = f"{raw_name} Shire Council"
            else:
                name = f"{raw_name} City Council"
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_AUTHORITY, name,
                    f"filename: {filename}",
                    confidence=0.78,
                    notes="council name inferred from filename title",
                )
            ]

    # 5. Filename-derived: skip common prefix words, use first meaningful token
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


def _extract_annual_amount(doc: Document, text: str, ai_client=None) -> list[Fact]:
    """Extract the council annual rates and charges amount.

    Architecture (evidence + validation system):
      1. Isolate the rates/charges section to avoid noise from notices/payment sections.
      2. Collect ALL candidates from every extraction strategy (no early return).
      3. Collect balance/outstanding values and flag any candidate that matches one.
      4. Rank candidates by evidence priority + confidence.
      5. Pick winner:
         a. If top candidate is not flagged and top-2 agree within $1 → use it (high conf).
         b. If top candidates conflict by > $1 → invoke LLM to classify rows (if available)
            and flag result as needs_review.
         c. If LLM also fails → use best non-balance candidate, flag needs_review.
      6. Emit `rates.council.needs_review = True` when evidence conflicts or is weak.
    """
    # Step 1 — isolate rates section
    section = _isolate_rates_section(text)

    # Step 2 — collect all candidates
    candidates = _collect_all_candidates(text, section)

    if not candidates:
        # LLM as true last resort when nothing at all was found
        if ai_client is not None:
            rows = extract_all_money_rows(section or text)
            if rows:
                classified = classify_rows_with_llm(rows, ai_client)
                if classified:
                    result = annual_from_classified_rows(classified)
                    if result:
                        total, quote = result
                        return [
                            _make_fact(
                                doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                                confidence=0.85,
                                notes=(
                                    "Annual council rates — LLM row classification "
                                    "(no rule-based candidates found)."
                                ),
                            ),
                            _make_fact(
                                doc, "rates.council.needs_review", True, quote,
                                confidence=0.99,
                                notes="No rule-based candidates; LLM-only result. Verify manually.",
                            ),
                        ]
        return []

    # Step 3 — flag balance/outstanding candidates
    balance_vals = _collect_balance_values(text)
    _mark_balance_candidates(candidates, balance_vals)

    # Step 4 — rank candidates
    ranked = sorted(candidates, key=_priority_score, reverse=True)
    valid = [c for c in ranked if not c.from_balance]
    if not valid:
        valid = ranked  # all flagged — still try, just with lower conf

    # Step 4b — cross-validate: if date_levied_scan and paid_plus_outstanding agree,
    # promote to a verified candidate with highest confidence.
    def _cand_by_source(source: str) -> "_Candidate | None":
        return next((c for c in valid if c.source == source), None)

    dl = _cand_by_source("date_levied_scan")
    pp = _cand_by_source("paid_plus_outstanding")
    if dl and pp and abs(dl.value - pp.value) <= 1.0:
        verified = _Candidate(
            value=dl.value,
            source="date_levied_verified_by_paid_due",
            confidence=0.99,
            quote=f"{dl.quote}; verified by paid+due: {pp.quote}",
        )
        valid.insert(0, verified)
        ranked.insert(0, verified)

    best = valid[0]

    # Step 5a — check if top-2 valid candidates agree within $1
    conflict = False
    conflict_note = ""
    second = valid[1] if len(valid) > 1 else None
    _STRONG_STRUCTURAL_SOURCES = {
        "explicit_subtotal[0]",
        "explicit_subtotal[1]",
        "explicit_subtotal[2]",
        "explicit_subtotal[3]",
        "total_charges_line",
        "levied_outstanding_table",
        "scrambled_levied_balance_block",
        "levied_balance_block",
    }
    _WEAK_CONFLICT_SOURCES = {
        "inline_charge_rows",
        "paid_plus_outstanding",
        "adjustments_plus_balance",
        "date_levied_scan",
    }
    if (
        second
        and abs(best.value - second.value) > 1.0
        and not (
            best.source in _STRONG_STRUCTURAL_SOURCES
            and second.source in _WEAK_CONFLICT_SOURCES
        )
    ):
        conflict = True
        conflict_note = (
            f"Top candidates conflict: {best.source}={_fmt(best.value)} vs "
            f"{second.source}={_fmt(second.value)}"
        )

    # Step 5b — trigger LLM when candidates conflict OR only one weak candidate exists.
    # "LLM classifies rows, never decides alone" — if LLM disagrees with all rule
    # candidates, keep the best rule candidate and emit needs_review.
    llm_used = False
    llm_note = ""
    _WEAK_SOLO_SOURCES = {"inline_charge_rows", "paid_plus_outstanding",
                          "adjustments_plus_balance", "date_levied_scan"}
    suspicious = (
        len(valid) == 1
        and valid[0].source in _WEAK_SOLO_SOURCES
        and not valid[0].from_balance
    )
    if ai_client is not None and (conflict or suspicious):
        rows = extract_all_money_rows(section or text)
        if rows:
            classified = classify_rows_with_llm(rows, ai_client)
            if classified:
                result = annual_from_classified_rows(classified)
                if result:
                    llm_val, llm_quote = result
                    # Accept LLM result only when it agrees with a rule candidate (within $1)
                    for c in valid[:3]:
                        if abs(c.value - llm_val) <= 1.0:
                            best = _Candidate(llm_val, "llm_arbitrated", c.confidence + 0.01, llm_quote)
                            conflict = False
                            suspicious = False
                            llm_used = True
                            break
                    if not llm_used:
                        # LLM disagrees with all rule results — keep rule winner, flag review.
                        # Design principle: LLM never overrides deterministic evidence.
                        conflict = True
                        llm_note = f"LLM={_fmt(llm_val)} disagrees with rule candidates"
                        conflict_note = (conflict_note + "; " + llm_note).lstrip("; ")
                        llm_used = True

    # Step 5c — if still conflict or suspicious, penalise confidence and flag review
    final_conf = best.confidence
    if conflict:
        final_conf = min(best.confidence, 0.78)

    # Build source note
    all_sources = ", ".join(f"{c.source}={_fmt(c.value)}" for c in valid[:4])
    note_parts = [f"Annual council rates — {best.source}: {_fmt(best.value)}."]
    if len(valid) > 1:
        note_parts.append(f"All candidates: {all_sources}.")
    if conflict:
        note_parts.append(f"CONFLICT: {conflict_note}.")
    if best.from_balance:
        note_parts.append("WARNING: best candidate matches a balance/outstanding value.")
    if llm_used:
        note_parts.append("LLM row classifier used for arbitration.")

    facts = [
        _make_fact(
            doc, P.RATES_COUNCIL_ANNUAL, _fmt(best.value), best.quote,
            confidence=final_conf,
            notes=" ".join(note_parts),
        )
    ]

    # Step 6 — emit needs_review when evidence is uncertain
    needs_review = (
        conflict
        or suspicious
        or best.from_balance
        or final_conf < 0.85
        or best.source.startswith(("quarterly_", "halfyearly_", "monthly_"))
    )
    if needs_review:
        review_reason = conflict_note or (
            f"Low-confidence source: {best.source}" if final_conf < 0.85 else
            "Instalment annualisation — verify against annual levy notice."
        )
        facts.append(
            _make_fact(
                doc, "rates.council.needs_review", True, best.quote,
                confidence=0.99,
                notes=review_reason,
            )
        )

    return facts


def _extract_annual_amount_v2(doc: Document, ai_client=None) -> list[Fact]:
    """Extract the annual amount using multi-source evidence and normalized rows."""
    candidates, primary_text, section, money_rows = _collect_document_candidates(doc)

    if not candidates:
        if ai_client is not None:
            rows = extract_all_money_rows(section or primary_text)
            if rows:
                classified = classify_rows_with_llm(rows, ai_client)
                if classified:
                    result = annual_from_classified_rows(classified)
                    if result:
                        total, quote = result
                        return [
                            _make_fact(
                                doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                                confidence=0.85,
                                notes=(
                                    "Annual council rates — LLM row classification "
                                    "(no rule-based candidates found)."
                                ),
                            ),
                            _make_fact(
                                doc, "rates.council.needs_review", True, quote,
                                confidence=0.99,
                                notes="No rule-based candidates; LLM-only result. Verify manually.",
                            ),
                        ]
        return []

    balance_vals = _collect_balance_values(primary_text)
    _mark_balance_candidates(candidates, balance_vals)

    ranked = sorted(candidates, key=_priority_score, reverse=True)
    valid = [c for c in ranked if not c.from_balance]
    if not valid:
        valid = ranked

    best = valid[0]
    second = valid[1] if len(valid) > 1 else None

    strong_structural_sources = {
        "explicit_subtotal[0]",
        "explicit_subtotal[1]",
        "explicit_subtotal[2]",
        "explicit_subtotal[3]",
        "total_charges_line",
        "levied_outstanding_table",
        "scrambled_levied_balance_block",
        "levied_balance_block",
        "moneyrow_annual_total_row",
        "current_levied_matrix_row",
        "current_rates_date_levied_block",
        "current_rates_levied_line",
        "zero_adjustments_total_due",
        "date_levied_verified_by_paid_due",
    }
    weak_conflict_sources = {
        "inline_charge_rows",
        "moneyrow_charge_rows",
        "paid_plus_outstanding",
        "adjustments_plus_balance",
        "date_levied_scan",
    }
    weak_solo_sources = weak_conflict_sources.copy()

    conflict = False
    conflict_note = ""
    if (
        second
        and abs(best.value - second.value) > 1.0
        and not (
            best.source in strong_structural_sources
            and second.source in weak_conflict_sources
        )
    ):
        conflict = True
        conflict_note = (
            f"Top candidates conflict: {best.source}={_fmt(best.value)} vs "
            f"{second.source}={_fmt(second.value)}"
        )

    suspicious = (
        len(valid) == 1
        and valid[0].source in weak_solo_sources
        and not valid[0].from_balance
    )

    llm_used = False
    if ai_client is not None and (conflict or suspicious):
        rows = extract_all_money_rows(section or primary_text)
        if rows:
            classified = classify_rows_with_llm(rows, ai_client)
            if classified:
                result = annual_from_classified_rows(classified)
                if result:
                    llm_val, llm_quote = result
                    for candidate in valid[:3]:
                        if abs(candidate.value - llm_val) <= 1.0:
                            best = _Candidate(
                                llm_val,
                                "llm_arbitrated",
                                candidate.confidence + 0.01,
                                llm_quote,
                            )
                            conflict = False
                            suspicious = False
                            llm_used = True
                            break
                    if not llm_used:
                        conflict = True
                        disagreement = f"LLM={_fmt(llm_val)} disagrees with rule candidates"
                        conflict_note = f"{conflict_note}; {disagreement}".strip("; ")
                        llm_used = True

    final_conf = best.confidence
    if conflict:
        final_conf = min(final_conf, 0.78)

    non_zero_balances = sorted(v for v in balance_vals if v > 0)
    lower_than_balance = bool(non_zero_balances and best.value + 1.0 < max(non_zero_balances))
    unusually_low = best.value < 300.0 and best.source not in strong_structural_sources
    unknown_table_shape = any(
        len(row.amounts) >= 2 and _classify_row_label(row.label) == "unknown"
        for row in money_rows
    )
    charge_sum = next((c for c in valid if c.source == "moneyrow_charge_rows"), None)
    if charge_sum and best.source != "moneyrow_charge_rows" and abs(best.value - charge_sum.value) > 1.0:
        conflict = True
        final_conf = min(final_conf, 0.78)
        disagreement = (
            f"Charge-row sum disagrees: moneyrow_charge_rows={_fmt(charge_sum.value)} vs "
            f"{best.source}={_fmt(best.value)}"
        )
        conflict_note = f"{conflict_note}; {disagreement}".strip("; ")

    all_sources = ", ".join(f"{c.source}={_fmt(c.value)}" for c in valid[:4])
    note_parts = [f"Annual council rates — {best.source}: {_fmt(best.value)}."]
    if money_rows:
        note_parts.append(f"Normalized money rows: {len(money_rows)}.")
    if len(valid) > 1:
        note_parts.append(f"All candidates: {all_sources}.")
    if conflict:
        note_parts.append(f"CONFLICT: {conflict_note}.")
    if best.from_balance:
        note_parts.append("WARNING: best candidate matches a balance/outstanding value.")
    if lower_than_balance:
        note_parts.append("WARNING: annual candidate is lower than the outstanding balance evidence.")
    if unusually_low:
        note_parts.append("WARNING: annual candidate is unusually low; verify manually.")
    if unknown_table_shape:
        note_parts.append("WARNING: detected multi-amount table rows with unknown structure.")
    if llm_used:
        note_parts.append("LLM row classifier used for arbitration.")

    facts = [
        _make_fact(
            doc,
            P.RATES_COUNCIL_ANNUAL,
            _fmt(best.value),
            best.quote,
            confidence=final_conf,
            notes=" ".join(note_parts),
        )
    ]

    needs_review = (
        conflict
        or suspicious
        or best.from_balance
        or lower_than_balance
        or unusually_low
        or unknown_table_shape
        or final_conf < 0.85
        or best.source.startswith(("quarterly_", "halfyearly_", "monthly_"))
    )
    if needs_review:
        review_reason = conflict_note or (
            "Annual amount lower than outstanding balance evidence." if lower_than_balance else
            "Annual amount unusually low." if unusually_low else
            "Detected unknown multi-amount table structure." if unknown_table_shape else
            f"Low-confidence source: {best.source}" if final_conf < 0.85 else
            "Instalment annualisation — verify against annual levy notice."
        )
        facts.append(
            _make_fact(
                doc,
                "rates.council.needs_review",
                True,
                best.quote,
                confidence=0.99,
                notes=review_reason,
            )
        )

    return facts


def _extract_annual_amount_v3(doc: Document, ai_client=None) -> list[Fact]:
    """Extract the annual amount using multi-source evidence and consensus ranking."""
    candidates, primary_text, section, money_rows = _collect_document_candidates(doc)

    if not candidates:
        if ai_client is not None:
            rows = extract_all_money_rows(section or primary_text)
            if rows:
                classified = classify_rows_with_llm(rows, ai_client)
                if classified:
                    result = annual_from_classified_rows(classified)
                    if result:
                        total, quote = result
                        return [
                            _make_fact(
                                doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                                confidence=0.85,
                                notes=(
                                    "Annual council rates â€” LLM row classification "
                                    "(no rule-based candidates found)."
                                ),
                            ),
                            _make_fact(
                                doc, "rates.council.needs_review", True, quote,
                                confidence=0.99,
                                notes="No rule-based candidates; LLM-only result. Verify manually.",
                            ),
                        ]
        return []

    balance_vals = _collect_balance_values(primary_text)
    _mark_balance_candidates(candidates, balance_vals)

    ranked = sorted(candidates, key=_priority_score, reverse=True)
    valid = [c for c in ranked if not c.from_balance]
    if not valid:
        valid = ranked

    strong_structural_sources = {
        "explicit_subtotal[0]",
        "explicit_subtotal[1]",
        "explicit_subtotal[2]",
        "explicit_subtotal[3]",
        "total_charges_line",
        "levied_outstanding_table",
        "scrambled_levied_balance_block",
        "levied_balance_block",
        "moneyrow_annual_total_row",
        "current_levied_matrix_row",
        "current_rates_date_levied_block",
        "current_rates_levied_line",
        "zero_adjustments_total_due",
        "date_levied_verified_by_paid_due",
    }
    weak_conflict_sources = {
        "inline_charge_rows",
        "moneyrow_charge_rows",
        "paid_plus_outstanding",
        "adjustments_plus_balance",
        "date_levied_scan",
    }

    non_zero_balances = sorted(v for v in balance_vals if v > 0)

    best = valid[0]
    explicit_candidate = next(
        (
            c for c in valid
            if c.source.startswith("explicit_subtotal") or c.source == "total_charges_line"
        ),
        None,
    )
    if explicit_candidate and abs(explicit_candidate.value - best.value) <= 1.0:
        best = explicit_candidate

    groups: list[list[_Candidate]] = []
    for candidate in valid:
        placed = False
        for group in groups:
            if abs(group[0].value - candidate.value) <= 1.0:
                group.append(candidate)
                placed = True
                break
        if not placed:
            groups.append([candidate])
    groups.sort(
        key=lambda group: (
            len(group),
            max(_priority_score(candidate) for candidate in group),
        ),
        reverse=True,
    )
    best_group = next((group for group in groups if best in group), [])
    if (
        groups
        and len(groups[0]) >= 2
        and len(groups[0]) > len(best_group)
        and (
            best.source not in strong_structural_sources
            or bool(non_zero_balances and best.value + 1.0 < max(non_zero_balances))
        )
    ):
        best = sorted(groups[0], key=_priority_score, reverse=True)[0]

    second = next((c for c in valid if c is not best), None)
    conflict = False
    conflict_note = ""
    if (
        second
        and abs(best.value - second.value) > 1.0
        and not (
            best.source in strong_structural_sources
            and second.source in weak_conflict_sources
        )
    ):
        conflict = True
        conflict_note = (
            f"Top candidates conflict: {best.source}={_fmt(best.value)} vs "
            f"{second.source}={_fmt(second.value)}"
        )

    weak_solo_sources = weak_conflict_sources.copy()
    suspicious = (
        len(valid) == 1
        and valid[0].source in weak_solo_sources
        and not valid[0].from_balance
    )

    lower_than_balance = bool(non_zero_balances and best.value + 1.0 < max(non_zero_balances))
    unusually_low = best.value < 300.0 and best.source not in strong_structural_sources
    unknown_table_shape = (
        any(len(row.amounts) >= 2 and _classify_row_label(row.label) == "unknown" for row in money_rows)
        and best.source not in strong_structural_sources
    )

    # ------------------------------------------------------------------
    # Omni perception sub-agent — runs BEFORE existing LLM arbitration
    # when the rule-based result looks weak / conflicting / suspicious.
    # ------------------------------------------------------------------

    # Snapshot rule candidates for debug logging and the verifier prompt.
    rule_candidates_list = [(c.source, c.value) for c in valid[:6]]

    # Compute the two new trigger signals.
    single_weak_candidate = (
        len(valid) == 1
        and valid[0].source in {
            "inline_charge_rows", "date_levied_scan",
            "paid_plus_outstanding", "adjustments_plus_balance",
        }
        and not valid[0].from_balance
    )
    candidate_disagrees_with_total_due = (
        bool(non_zero_balances)
        and best.value + 1.0 < max(non_zero_balances)
        and best.source not in strong_structural_sources
    )

    omni_used = False
    omni_rows: list = []
    omni_reason = ""
    original_best_value = best.value
    original_best_source = best.source

    if ai_client is not None and should_call_omni(
        best_source=best.source,
        best_value=best.value,
        conflict=conflict,
        suspicious=suspicious,
        from_balance=best.from_balance,
        lower_than_balance=lower_than_balance,
        unknown_table_shape=unknown_table_shape,
        candidate_count=len(valid),
        single_weak_candidate=single_weak_candidate,
        candidate_disagrees_with_total_due=candidate_disagrees_with_total_due,
    ):
        omni_reason = (
            f"conflict={conflict} suspicious={suspicious} single_weak={single_weak_candidate} "
            f"disagrees_with_total_due={candidate_disagrees_with_total_due} "
            f"from_balance={best.from_balance} lower_than_balance={lower_than_balance} "
            f"unknown_table={unknown_table_shape} "
            f"best={best.source}={_fmt(best.value)}"
        )
        omni_rows = classify_table_with_omni(
            text=section or primary_text,
            ai_client=ai_client,
            doc_type="council",
        )
        omni_result = annual_from_omni_rows(omni_rows)
        _model_name = getattr(ai_client, "model", "unknown")
        if omni_result:
            omni_val, omni_quote = omni_result
            omni_ok, omni_reject_reason = validate_omni_candidate(
                omni_val, omni_rows, set(balance_vals)
            )
            if omni_ok:
                candidates.append(
                    _Candidate(omni_val, "omni_structured_rows", 0.86, omni_quote)
                )
                # Re-rank including the new Omni candidate
                valid_with_omni = sorted(
                    [c for c in candidates if not c.from_balance],
                    key=_priority_score,
                    reverse=True,
                )
                if valid_with_omni:
                    best = valid_with_omni[0]
                    if best.source == "omni_structured_rows":
                        conflict = False
                        suspicious = False
                omni_used = True

                # Verifier pass: when Omni's answer significantly differs from the
                # original rule-best, ask the verifier whether the original was correct.
                verifier_supported: bool | None = None
                verifier_reason_str: str | None = None
                omni_disagrees = abs(omni_val - original_best_value) > 1.0
                if omni_disagrees and original_best_source not in strong_structural_sources:
                    try:
                        vresult = verify_extraction(
                            doc_type="council_rates",
                            final_amount=original_best_value,
                            rule_source=original_best_source,
                            rule_candidates=rule_candidates_list,
                            rows=omni_rows,
                            ai_client=ai_client,
                        )
                        verifier_supported = vresult.supported
                        verifier_reason_str = vresult.reason
                        # If verifier says original rule amount is wrong AND
                        # Omni's amount is already selected as best, keep it.
                        # If Omni is not yet best (e.g. validation passed but
                        # rank didn't elevate it), force-select Omni here.
                        if not vresult.supported and best.source != "omni_structured_rows":
                            best = _Candidate(omni_val, "omni_structured_rows", 0.86, omni_quote)
                            conflict = False
                            suspicious = False
                    except Exception:
                        pass

                write_debug_log(
                    filename=doc.filename or "",
                    doc_type="council",
                    called=True,
                    reason=omni_reason,
                    model=_model_name,
                    rows=omni_rows,
                    candidate=omni_val,
                    accepted=True,
                    rule_candidates=rule_candidates_list,
                    verifier_supported=verifier_supported,
                    verifier_reason=verifier_reason_str,
                )
            else:
                write_debug_log(
                    filename=doc.filename or "",
                    doc_type="council",
                    called=True,
                    reason=omni_reason,
                    model=_model_name,
                    rows=omni_rows,
                    candidate=omni_val,
                    accepted=False,
                    rule_candidates=rule_candidates_list,
                )
        else:
            write_debug_log(
                filename=doc.filename or "",
                doc_type="council",
                called=True,
                reason=omni_reason,
                model=_model_name,
                rows=omni_rows,
                candidate=None,
                accepted=False,
                rule_candidates=rule_candidates_list,
            )

    # ------------------------------------------------------------------
    # Existing LLM arbitration (row classifier) — conflict / suspicious
    # fallback when Omni didn't resolve the conflict.
    # ------------------------------------------------------------------
    llm_used = False
    if ai_client is not None and (conflict or suspicious) and not omni_used:
        rows_for_llm = extract_all_money_rows(section or primary_text)
        if rows_for_llm:
            classified = classify_rows_with_llm(rows_for_llm, ai_client)
            if classified:
                result = annual_from_classified_rows(classified)
                if result:
                    llm_val, llm_quote = result
                    for candidate in valid[:3]:
                        if abs(candidate.value - llm_val) <= 1.0:
                            best = _Candidate(
                                llm_val,
                                "llm_arbitrated",
                                candidate.confidence + 0.01,
                                llm_quote,
                            )
                            conflict = False
                            suspicious = False
                            llm_used = True
                            break
                    if not llm_used:
                        conflict = True
                        disagreement = f"LLM={_fmt(llm_val)} disagrees with rule candidates"
                        conflict_note = f"{conflict_note}; {disagreement}".strip("; ")
                        llm_used = True

    final_conf = best.confidence
    if conflict:
        final_conf = min(final_conf, 0.78)

    # Recompute derived flags using the (possibly updated) best
    lower_than_balance = bool(non_zero_balances and best.value + 1.0 < max(non_zero_balances))
    unusually_low = best.value < 300.0 and best.source not in strong_structural_sources
    unknown_table_shape = (
        any(len(row.amounts) >= 2 and _classify_row_label(row.label) == "unknown" for row in money_rows)
        and best.source not in strong_structural_sources
    )
    charge_sum = next((c for c in valid if c.source == "moneyrow_charge_rows"), None)
    if (
        charge_sum
        and best.source != "moneyrow_charge_rows"
        and not (best.source.startswith("explicit_subtotal") or best.source == "total_charges_line")
        and abs(best.value - charge_sum.value) > 1.0
    ):
        conflict = True
        final_conf = min(final_conf, 0.78)
        disagreement = (
            f"Charge-row sum disagrees: moneyrow_charge_rows={_fmt(charge_sum.value)} vs "
            f"{best.source}={_fmt(best.value)}"
        )
        conflict_note = f"{conflict_note}; {disagreement}".strip("; ")

    adjustments_match = next(
        (
            c for c in valid
            if c.source == "adjustments_plus_balance" and abs(c.value - best.value) <= 1.0
        ),
        None,
    )

    all_sources = ", ".join(f"{c.source}={_fmt(c.value)}" for c in valid[:4])
    note_parts = [f"Annual council rates â€” {best.source}: {_fmt(best.value)}."]
    note_parts.append(f"Evidence: {best.quote}.")
    if money_rows:
        note_parts.append(f"Normalized money rows: {len(money_rows)}.")
    if len(valid) > 1:
        note_parts.append(f"All candidates: {all_sources}.")
    if conflict:
        note_parts.append(f"CONFLICT: {conflict_note}.")
    if best.from_balance:
        note_parts.append("WARNING: best candidate matches a balance/outstanding value.")
    if lower_than_balance:
        note_parts.append("WARNING: annual candidate is lower than the outstanding balance evidence.")
    if unusually_low:
        note_parts.append("WARNING: annual candidate is unusually low; verify manually.")
    if unknown_table_shape:
        note_parts.append("WARNING: detected multi-amount table rows with unknown structure.")
    if adjustments_match:
        note_parts.append("Supporting evidence: negative adjustments plus final balance agrees.")
    table_match = next(
        (
            c for c in valid
            if c.source == "levied_column_table" and abs(c.value - best.value) <= 1.0
        ),
        None,
    )
    if table_match:
        note_parts.append(f"Supporting table evidence: {table_match.quote}.")
    if omni_used:
        note_parts.append("Nemotron Omni perception sub-agent used for table structure analysis.")
    if llm_used:
        note_parts.append("LLM row classifier used for arbitration.")

    facts = [
        _make_fact(
            doc,
            P.RATES_COUNCIL_ANNUAL,
            _fmt(best.value),
            best.quote,
            confidence=final_conf,
            notes=" ".join(note_parts),
        )
    ]

    needs_review = (
        conflict
        or suspicious
        or best.from_balance
        or lower_than_balance
        or unusually_low
        or unknown_table_shape
        or final_conf < 0.85
        or best.source.startswith(("quarterly_", "halfyearly_", "monthly_"))
    )
    if needs_review:
        review_reason = conflict_note or (
            "Annual amount lower than outstanding balance evidence." if lower_than_balance else
            "Annual amount unusually low." if unusually_low else
            "Detected unknown multi-amount table structure." if unknown_table_shape else
            f"Low-confidence source: {best.source}" if final_conf < 0.85 else
            "Instalment annualisation â€” verify against annual levy notice."
        )
        facts.append(
            _make_fact(
                doc,
                "rates.council.needs_review",
                True,
                best.quote,
                confidence=0.99,
                notes=review_reason,
            )
        )

    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_council_rates_certificate_facts(doc: Document, *, ai_client=None) -> list[Fact]:
    """Extract facts from a council rates notice or land information statement.

    ai_client — optional AIClient instance.  When provided, a LLM row-classifier
    is used as a last-resort fallback if all rule-based strategies fail.
    Pass None (the default) to keep behaviour fully deterministic.
    """
    # Use raw_text for extraction (preserves original spacing needed for
    # table-layout patterns), but include normalized_text in the trigger
    # check so documents are recognised even when raw_text is None/empty.
    raw = doc.raw_text or ""
    norm = doc.normalized_text or ""
    text = raw or norm
    if not text:
        return []

    filename = doc.filename or ""
    # Search both raw and normalised text for the trigger so it fires even
    # if pypdf produced slightly unusual whitespace/encoding in raw_text.
    haystack = f"{filename}\n{raw}\n{norm}"

    if not _DOC_TRIGGERS.search(haystack):
        return []

    # Water authority PDFs sometimes contain a "Rates Certificate" section header
    # which fires _DOC_TRIGGERS. Exclude them early to prevent producing a fake
    # council name from the water document filename.
    if _WATER_AUTHORITY_EXCLUSION.search(haystack):
        return []

    facts: list[Fact] = []
    facts.extend(_extract_council_name(doc, text, filename))
    facts.extend(_extract_annual_amount_v3(doc, ai_client=ai_client))
    return facts
