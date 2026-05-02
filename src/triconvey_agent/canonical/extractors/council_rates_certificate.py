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
from datetime import date

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.extractors.council_rates_row_classifier import (
    annual_from_classified_rows,
    classify_rows_with_llm,
    extract_all_money_rows,
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
    r"\b(?:general\s+rate|rates?|rate|area|chg|charge|waste|garbage|recycling|"
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

# Simpler scanner: find every "Date Levied  DD/MM/YYYY  $X.XX" occurrence.
# Sum of ALL lines (including ESVF) = full annual charge.
_DATE_LEVIED_SCAN_RE = re.compile(
    r"Date\s+Levied\s+\d{1,2}[/\-]\d{1,2}[/\-]\d{4}[^\n$]*\$\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
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

    Detection hierarchy — evidence-based, not council-specific:
      1.  Explicit annual subtotal line (Bass Coast, Baw Baw, Knox, Ballarat)
      1b. Explicit TOTAL CHARGES line (Greater Dandenong style)
      2.  Sum of current-year charge rows — stacked layout then inline
      3.  Sum of LEVIED column in explicit table layout
      4.  Paid + outstanding + rebates/credits formula
      5.  Instalment annualisation fallback
      6.  LLM row classifier (only when ai_client provided and steps 1–5 all failed)
    """

    # ── 1. Explicit annual subtotal line
    #       Covers: "Rates & Charges Sub Total for 2025/26 $X" (Bass Coast),
    #       "Current Years Rates and Charges Sub Total $X" (Baw Baw),
    #       "Sub total $X" (Knox), "Current Total: $X" (Ballarat).
    #       Checked first because the subtotal is the gross levy before rebates and
    #       payments, which is what the annual amount should represent.
    for _pat in _SUBTOTAL_PATTERNS:
        m = _pat.search(text)
        if m:
            try:
                val = _parse_dollar(m.group(1))
                if val > 0:
                    return [
                        _make_fact(
                            doc, P.RATES_COUNCIL_ANNUAL, _fmt(val),
                            _compact(m.group(0)),
                            confidence=0.97,
                            notes="Annual council rates — explicit subtotal line.",
                        )
                    ]
            except ValueError:
                pass

    # ── 1b. Explicit "TOTAL CHARGES $X" line
    #        When the document states the total directly (Greater Dandenong style),
    #        use it instead of reconstructing from individual rows.  Confidence is
    #        higher than row-summing because the council computed it; checked after
    #        subtotal patterns because "Sub Total" is equally explicit and more common.
    tc = _find_total_charges(text)
    if tc:
        total, quote = tc
        return [
            _make_fact(
                doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                confidence=0.98,
                notes="Annual council rates — explicit TOTAL CHARGES line.",
            )
        ]

    # ── 1c. Two-column "Amounts Levied / Outstanding Amount" table
    #        (Central Goldfields and similar).  Anchors on the column-header text
    #        so we always read the Levied column, never the Outstanding column.
    #        Must run before generic row-summing, which would otherwise grab the
    #        first dollar amount per line and may pick the wrong column.
    levied_outstanding = _sum_levied_outstanding_table(text)
    if levied_outstanding:
        total, quote = levied_outstanding
        return [
            _make_fact(
                doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                confidence=0.97,
                notes="Annual council rates — Amounts Levied column (two-column table).",
            )
        ]

    # ── 2. Sum of current-year charge rows — two shapes handled in priority order:
    #
    #    Shape A (stacked): labels on separate lines above their amounts (Monash).
    #       Charges:
    #         Residential Rate          ← label lines
    #         Recycle and Waste
    #         ESVF
    #         1,839.75                  ← amount lines (same count, same order)
    #         65.00
    #         355.70
    #
    #    Shape B (inline): label and amount on the same line (Brimbank, Maroondah).
    #       General Rate $1,558.95
    #       Waste Service Charge 465.00
    #
    #    Stacked is tried first because it is more specific (requires a "Charges:"
    #    anchor and matching label/amount counts).  If no "Charges:" block is
    #    found, the inline scanner runs over the full text.

    stacked = _sum_stacked_charge_block(text)
    if stacked:
        total, quote = stacked
        return [
            _make_fact(
                doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                confidence=0.97,
                notes=f"Annual council rates — stacked charges block: {_fmt(total)}",
            )
        ]

    charge_rows = _sum_current_charge_rows(text)
    if charge_rows:
        total, quote = charge_rows
        return [
            _make_fact(
                doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                confidence=0.96,
                notes=f"Annual council rates — inline charge rows: {_fmt(total)}",
            )
        ]

    # ── 3. Sum of LEVIED column in explicit "RATES & CHARGES LEVIED REBATES BALANCE" table
    #       Fallback for councils that use a formal multi-column table layout but where
    #       step 2 failed to detect individual rows (e.g. unusual label phrasing).
    table_totals = _extract_table_totals(text)
    if table_totals:
        levied_total, rebate_total, balance_total, quote = table_totals
        note = f"Annual council rates — levied column total {_fmt(levied_total)}"
        if rebate_total > 0:
            note += f" (rebates {_fmt(rebate_total)} not deducted)"
        return [
            _make_fact(
                doc, P.RATES_COUNCIL_ANNUAL, _fmt(levied_total), quote,
                confidence=0.96,
                notes=note,
            )
        ]

    # ── 4. Paid + outstanding + rebates/credits formula
    #       abs(payments/credits) + final balance = annual levy.
    #       Used when no charge rows are listed (e.g. Ballarat fully-paid case).
    adjustments = _find_negative_adjustments(text)
    final_balance = _find_final_balance_amount(text)
    if adjustments and final_balance:
        adjustments_total = round(sum(value for value, _ in adjustments), 2)
        balance_value, balance_ctx = final_balance
        total = round(adjustments_total + balance_value, 2)
        parts = " + ".join(_fmt(value) for value, _ in adjustments)
        quote = "; ".join([ctx for _, ctx in adjustments] + [balance_ctx])
        return [
            _make_fact(
                doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                confidence=0.95,
                notes=(
                    f"Annual = payments/credits {parts} + balance {_fmt(balance_value)} "
                    f"= {_fmt(total)}"
                ),
            )
        ]

    paid_result = _find_paid_amount(text)
    paid_pos = 0
    if paid_result:
        m_paid = _PAID_KW_RE.search(text)
        paid_pos = m_paid.start() if m_paid else 0

    outstanding_result = _find_outstanding_amount(text, paid_pos=paid_pos)

    if paid_result and outstanding_result:
        try:
            paid_digits, paid_ctx = paid_result
            out_digits, out_ctx = outstanding_result
            paid_val = _parse_dollar(paid_digits)
            out_val = _parse_dollar(out_digits)
            total = round(paid_val + out_val, 2)
            total_str = _fmt(total)
            quote = f"{paid_ctx}; {out_ctx}"
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, total_str, quote,
                    confidence=0.95,
                    notes=(
                        f"Annual = paid {_fmt(paid_val)} + outstanding {_fmt(out_val)} "
                        f"= {total_str}"
                    ),
                )
            ]
        except (ValueError, AttributeError):
            pass

    # ── 5a. "Annual Council Rates and Charges YYYY/YYYY $X" explicit label
    m = _ANNUAL_COUNCIL_RATES_RE.search(text)
    if m:
        return [
            _make_fact(
                doc, P.RATES_COUNCIL_ANNUAL, m.group(1),
                _compact(m.group(0)),
                confidence=0.88,
                notes="Annual council rates — explicit annual label.",
            )
        ]

    # ── 4. Generic annual labels ("total annual rates/charges", "rates for the year")
    for pattern in _ANNUAL_LABELS:
        m = pattern.search(text)
        if m:
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, m.group(1),
                    _compact(m.group(0)),
                    confidence=0.84,
                    notes="Annual council rates — generic annual label",
                )
            ]

    # ── 5. Sum of ALL "Date Levied DD/MM/YYYY $X" lines (Brimbank table format)
    #       Includes ESVF — full annual levy equals all levied charges.
    #       Excludes only: arrears, interest, payments, rebates, adjustments.
    scan_matches = list(_DATE_LEVIED_SCAN_RE.finditer(text))
    if scan_matches:
        total = 0.0
        included: list[str] = []
        for sm in scan_matches:
            start = sm.start()
            lookback = text[max(0, start - 120): start]
            label_lines = [ln.strip() for ln in lookback.split("\n") if ln.strip()]
            label = label_lines[-1] if label_lines else ""
            if _EXCLUDE_DATE_LEVIED_RE.search(label):
                continue
            try:
                total += _parse_dollar(sm.group(1))
                included.append(f"${sm.group(1)}")
            except (ValueError, AttributeError):
                pass
        if total > 0 and len(included) >= 2:
            annual_str = _fmt(total)
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, annual_str,
                    f"Sum of {len(included)} Date Levied lines: {' + '.join(included)} = {annual_str}",
                    confidence=0.88,
                    notes=f"Annual council rates: sum of all {len(included)} levied charge lines (incl. ESVF)",
                )
            ]

    # ── 6. Quarterly instalment × 4
    m = _QUARTERLY_LABELS.search(text)
    if m:
        try:
            multiplier = _period_multiplier_from_range(text) or 4
            annual = round(_parse_dollar(m.group(1)) * multiplier, 2)
            annual_str = _fmt(annual)
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, annual_str,
                    _compact(m.group(0)),
                    confidence=0.85,
                    notes=f"Annual council rates: quarterly {m.group(1)} × {multiplier}",
                )
            ]
        except (ValueError, AttributeError):
            pass

    # ── 7. Half-yearly × 2
    m = _HALFYEARLY_LABELS.search(text)
    if m:
        try:
            multiplier = _period_multiplier_from_range(text) or 2
            annual = round(_parse_dollar(m.group(1)) * multiplier, 2)
            annual_str = _fmt(annual)
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, annual_str,
                    _compact(m.group(0)),
                    confidence=0.83,
                    notes=f"Annual council rates: half-yearly {m.group(1)} × {multiplier}",
                )
            ]
        except (ValueError, AttributeError):
            pass

    # ── 8. Monthly × 12
    m = _MONTHLY_LABELS.search(text)
    if m:
        try:
            annual = round(_parse_dollar(m.group(1)) * 12, 2)
            annual_str = _fmt(annual)
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, annual_str,
                    _compact(m.group(0)),
                    confidence=0.80,
                    notes=f"Annual council rates: monthly {m.group(1)} × 12",
                )
            ]
        except (ValueError, AttributeError):
            pass

    # ── 9. LLM row classifier — last resort when all rule-based steps failed.
    #       The LLM classifies every (label, amount) pair extracted from the text;
    #       the deterministic `annual_from_classified_rows` then computes the total.
    #       The LLM never guesses an amount — it only assigns a row type.
    if ai_client is not None:
        candidate_rows = extract_all_money_rows(text)
        if candidate_rows:
            classified = classify_rows_with_llm(candidate_rows, ai_client)
            if classified:
                result = annual_from_classified_rows(classified)
                if result:
                    total, quote = result
                    return [
                        _make_fact(
                            doc, P.RATES_COUNCIL_ANNUAL, _fmt(total), quote,
                            confidence=0.88,
                            notes=(
                                "Annual council rates — LLM row classification. "
                                "Rule-based extraction found no result; "
                                "LLM classified rows, deterministic calculator computed total."
                            ),
                        )
                    ]

    # All extraction strategies failed.
    # Return empty so the vendor form fallback (conf 0.40) can win via authority rules.
    return []


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
    facts.extend(_extract_annual_amount(doc, text, ai_client=ai_client))
    return facts
