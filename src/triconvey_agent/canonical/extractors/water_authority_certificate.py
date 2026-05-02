"""Water authority certificate extractor (all Victorian water authorities).

Reads a Section 158 Water Information Statement / Rates Certificate PDF
issued by a Victorian water authority and emits Facts at canonical
rates.water.* paths.

Annual amount extraction — priority order
-----------------------------------------
1. Explicit "Total annual charges $X" label (GWW format)                conf 0.99
2. Quarterly charge lines (DD-MM-YYYY to DD-MM-YYYY) × 4               conf 0.95
   e.g. Yarra Valley Water: $193.68 × 4 = $774.72
3. Daily rate lines ("X Days @ $Y Per Day = $Z") × 365.25              conf 0.80
   e.g. Central Highlands Water: 28 days × daily_rate → annual
   → emits "copy_rules_recommended" flag so UI can prompt for DB lookup
4. [Copy Rules] — database lookup by authority name (future feature)    conf 0.75
5. Vendor form                                                           conf 0.40 (last resort)

Registered as `rule:water_authority_certificate_v1`.
"""
from __future__ import annotations

import calendar
import re
from datetime import date

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:water_authority_certificate_v1"

# ---------------------------------------------------------------------------
# Anchors / regex
# ---------------------------------------------------------------------------

_KNOWN_AUTHORITIES = (
    "Yarra Valley Water",
    "South East Water",
    "City West Water",
    "Greater Western Water",
    "Western Water",
    "Barwon Water",
    "Coliban Water",
    "Goulburn Valley Water",
    "Central Highlands Water",
    "Central Highlands Region Water Corporation",
    "Gippsland Water",
    "Lower Murray Water",
    "North East Water",
    "South Gippsland Water",
    "Wannon Water",
    "East Gippsland Water",
    "GWMWater",
    "Grampians Wimmera Mallee Water",
    "Westernport Water",
)

_CERT_NO_RE = re.compile(
    r"(?:Information Statement|Rate Certificate No\.?:?|Certificate No\.?:?)\s*[:#]?\s*(\d{6,})",
    re.IGNORECASE,
)

_PROPERTY_ROW_RE = re.compile(
    r"Property Address\s+Lot\s*&\s*Plan\s+Property Number\s+Property Type\s*\n"
    r"(?P<address>.+?)\s+(?P<lot>\d+)\\(?P<plan>[A-Z]{0,3}\d+[A-Z]?)\s+(?P<propnum>\d+)\s+(?P<ptype>\w+)",
    re.IGNORECASE,
)

# Each charge line: label  period  amount  outstanding
_CHARGE_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z &/]+?)\s+"
    r"(?P<period>\d{2}-\d{2}-\d{4}\s+to\s+\d{2}-\d{2}-\d{4})\s+"
    r"(?P<amount>\$[\d,]+\.\d{2})\s+"
    r"(?P<outstanding>\$[\d,]+\.\d{2})\s*$",
    re.MULTILINE,
)

# Annual total shown explicitly — GWW format: "Total annual charges $737.01"
_TOTAL_ANNUAL_RE = re.compile(
    r"Total\s+annual\s+charges?\s+(\$[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Broader annual label fallback
_ANNUAL_RE = re.compile(
    r"(?:annual\s+(?:rates?|charges?|amount)|total\s+annual)[^\n$]{0,40}(\$[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# GWW per-line annual format: "Residential Water Service Charge  $224.24  Quarterly  $168.33  $168.33"
# Each line: label  annual_charge  frequency  ytd  outstanding
_GWW_ANNUAL_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z\s&/]+?)\s+"
    r"(?P<annual>\$[\d,]+\.\d{2})\s+"
    r"(?P<freq>Quarterly|Half[\s-]?yearly|Annual|Monthly)\s+"
    r"(?P<ytd>\$[\d,]+\.\d{2})\s+"
    r"(?P<outstanding>\$[\d,]+\.\d{2})\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_TOTAL_RE = re.compile(
    r"Total for This Property\s+(\$[\d,]+\.\d{2})", re.IGNORECASE
)
_TOTAL_ALT_RE = re.compile(
    r"Total (?:charges|amount)\s+(\$[\d,]+\.\d{2})", re.IGNORECASE
)

# ── Flexible period date — matches both DD-MM-YYYY and DD/MM/YYYY ────────────
_PERIOD_FLEX_RE = re.compile(
    r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})"
    r"\s+(?:to|[-–])\s+"
    r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})"
)

# Flexible charge lines — no outstanding column required, accepts slash dates
# Matches: "Label  DD/MM/YYYY  [to] DD/MM/YYYY  $amount" or with hyphens
_CHARGE_LINE_FLEX_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z &/\-]+?)\s+"
    r"(?P<from>\d{1,2}[/\-]\d{1,2}[/\-]\d{4})"
    r"(?:\s+(?:to|[-–])\s+|\s+)"
    r"(?P<to>\d{1,2}[/\-]\d{1,2}[/\-]\d{4})\s+"
    r"(?P<amount>\$[\d,]+\.\d{2})",
    re.MULTILINE | re.IGNORECASE,
)

# "Total current charges $X" / "Amount due $X" / "Total amount $X" etc.
_TOTAL_CURRENT_RE = re.compile(
    r"(?:"
    r"total\s+current\s+(?:charges?|amount)"
    r"|current\s+(?:charges?|amount)\s+(?:due|total)"
    r"|amount\s+(?:now\s+)?due\s+(?:this\s+(?:period|quarter|statement))?"
    r"|total\s+amount\s+due"
    r"|charges?\s+for\s+(?:this\s+)?(?:period|statement)"
    r"|statement\s+total"
    r"|total\s+due\s+(?:this\s+period)?"
    r"|balance\s+due"
    r")"
    r"[^\n$]{0,40}\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)

_GIPPSLAND_BILLING_PERIODS_RE = re.compile(
    r"Gippsland\s+Water\s+billing\s+periods:\s*"
    r"01\s+Jul\s+to\s+31\s+Oct,\s*01\s+Nov\s+to\s+28\s+Feb\s+and\s+01\s+Mar\s+to\s+30\s+June",
    re.IGNORECASE,
)

_GIPPSLAND_SERVICE_CHARGE_RE = re.compile(
    r"Water\s+Service\s+Charges\s+([\-]?\d[\d,]*\.\d{2})",
    re.IGNORECASE,
)

_GIPPSLAND_FIXED_SERVICE_RE = re.compile(
    r"(Water\s+Service\s+Charges|Fire\s+Service\s+Charges|Wastewater\s+Service\s+Charges)\s+([\-]?\d[\d,]*\.\d{2})",
    re.IGNORECASE,
)

_GIPPSLAND_CHARGE_BLOCK_RE = re.compile(
    r"Charges\s+levied\s+for\s+billing\s+period:\s*(.*?)"
    r"Gippsland\s+Water\s+billing\s+periods:",
    re.IGNORECASE | re.DOTALL,
)

# Daily rate format used by Central Highlands Water (dollars) and Goulburn Valley (cents).
# Central Highlands: "Water Service Charge: From 20/02/2026 To 20/03/2026 = 28 Days @ 0.6669 Per Day = $18.67"
# Goulburn Valley:   "Sewerage Service Fee: From 01/01/2026 To 04/02/2026 = 35 Days @ 135.83¢ Per Day = $47.54"
# Optional ¢/c unit captured in group "unit" — if present, daily rate is in cents, divide by 100.
_DAILY_RATE_LINE_RE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z\s/]+?):\s+"
    r"From\s+\d{1,2}/\d{2}/\d{4}\s+To\s+\d{1,2}/\d{2}/\d{4}"
    r"\s*=\s*(?P<days>\d+)\s+Days?\s+@\s+(?P<daily>[\d.]+)(?P<unit>[¢c])?\s+Per\s+Day"
    r"\s*=\s*\$(?P<period_total>[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Goulburn Valley Water cents-per-day fixed charge format:
# "Sewerage Service Charge  135.83¢/day" or "135.83 cents per day" or "135.83 c/day"
_CENTS_PER_DAY_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z\s/&]+?)\s+"
    r"(?P<cents>[\d]+\.[\d]{2,4})\s*(?:¢|c|cents?)\s*(?:/\s*day|per\s+day)",
    re.IGNORECASE | re.MULTILINE,
)

# Goulburn Valley usage line: "Water Usage  23kL @ 131.02¢/kL" within a billing period
_USAGE_KL_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z\s/&]+?)\s+"
    r"(?P<volume>[\d]+(?:\.\d+)?)\s*kL?\s*@\s*"
    r"(?P<rate>[\d]+\.[\d]{2,4})\s*(?:¢|c|cents?)\s*/\s*kL?",
    re.IGNORECASE | re.MULTILINE,
)

# Billing period line: "Billing Period: DD/MM/YYYY to DD/MM/YYYY" or similar
_BILLING_PERIOD_RE = re.compile(
    r"[Bb]illing\s+[Pp]eriod[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})"
    r"\s+(?:to|[-–])\s+(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})",
    re.IGNORECASE,
)

_DATE_OF_ISSUE_RE = re.compile(
    r"Date of Issue\s+(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})",
    re.IGNORECASE,
)

_PERIOD_RE = re.compile(
    r"(\d{2})-(\d{2})-(\d{4})\s+to\s+(\d{2})-(\d{2})-(\d{4})"
)

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "sept": "09", "oct": "10", "nov": "11", "dec": "12",
}


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


def _month_to_num(month: str) -> str:
    return _MONTH_MAP.get(month.lower(), "01")


def _parse_dollar(s: str) -> float:
    return float(s.replace("$", "").replace(",", "").strip())


def _period_days(period_str: str) -> int | None:
    """Return number of days in a 'DD-MM-YYYY to DD-MM-YYYY' period string."""
    m = _PERIOD_RE.search(period_str) or _PERIOD_FLEX_RE.search(period_str)
    if not m:
        return None
    d1, mo1, y1, d2, mo2, y2 = [int(x) for x in m.groups()]
    try:
        start = date(y1, mo1, d1)
        end = date(y2, mo2, d2)
        return (end - start).days + 1
    except ValueError:
        return None


def _period_days_flex(from_str: str, to_str: str) -> int | None:
    """Return number of days between two date strings (DD/MM/YYYY or DD-MM-YYYY)."""
    def parse_date(s: str) -> date | None:
        parts = re.split(r"[/\-]", s.strip())
        if len(parts) != 3:
            return None
        try:
            d, mo, y = int(parts[0]), int(parts[1]), int(parts[2])
            return date(y, mo, d)
        except ValueError:
            return None
    s = parse_date(from_str)
    e = parse_date(to_str)
    if s is None or e is None:
        return None
    return (e - s).days + 1


def _billing_multiplier(days: int | None) -> tuple[str, int]:
    """Return (period_label, multiplier_to_annual) from a period's day count."""
    if days is None:
        return "quarterly", 4  # Victorian default
    if days <= 100:
        return "quarterly", 4
    if days <= 200:
        return "semi_annual", 2
    if days <= 380:
        return "annual", 1
    return "multi_year", 1


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------


def _extract_authority(doc: Document, text: str) -> list[Fact]:
    for name in _KNOWN_AUTHORITIES:
        if name.lower() in text.lower():
            # Always emit the canonical title-case name regardless of how the
            # document styles it (e.g. "YARRA VALLEY WATER" → "Yarra Valley Water").
            return [
                _make_fact(
                    doc,
                    P.RATES_WATER_AUTHORITY,
                    name,
                    quote=name,
                    confidence=0.99,
                )
            ]
    return []


def _extract_certificate_number(doc: Document, text: str) -> list[Fact]:
    m = _CERT_NO_RE.search(text)
    if not m:
        return []
    return [
        _make_fact(
            doc, P.RATES_WATER_CERTIFICATE_NUMBER, m.group(1), m.group(0)
        )
    ]


def _extract_property_row(doc: Document, text: str) -> list[Fact]:
    m = _PROPERTY_ROW_RE.search(text)
    if not m:
        return []
    address = _compact(m.group("address"))
    lot = m.group("lot")
    plan = m.group("plan")
    propnum = m.group("propnum")
    ptype = m.group("ptype")
    quote = _compact(m.group(0))

    return [
        _make_fact(
            doc, P.PROPERTY_ADDRESS, address, quote, confidence=0.92,
            notes="from water authority rates table",
        ),
        _make_fact(doc, P.PROPERTY_LOT_NUMBER, lot, quote, confidence=0.95),
        _make_fact(doc, P.PROPERTY_PLAN_NUMBER, plan, quote, confidence=0.95),
        _make_fact(doc, P.RATES_WATER_PROPERTY_NUMBER, propnum, quote),
        _make_fact(doc, P.RATES_WATER_ACCOUNT_NUMBER, propnum, quote),
        _make_fact(doc, P.RATES_WATER_PROPERTY_TYPE, ptype, quote),
    ]


def _extract_charges(doc: Document, text: str) -> list[Fact]:
    """Extract individual charge lines and return facts + sum of current-period amounts."""
    facts: list[Fact] = []
    matches = list(_CHARGE_LINE_RE.finditer(text))
    if not matches:
        return facts

    period_seen = False
    period_days_val: int | None = None

    for idx, m in enumerate(matches):
        label = _compact(m.group("label"))
        period = m.group("period")
        amount = m.group("amount")
        outstanding = m.group("outstanding")
        quote = _compact(m.group(0))

        facts.append(_make_fact(doc, P.water_charge(idx, "label"), label, quote))
        facts.append(_make_fact(doc, P.water_charge(idx, "period"), period, quote))
        facts.append(_make_fact(doc, P.water_charge(idx, "amount"), amount, quote))
        facts.append(
            _make_fact(doc, P.water_charge(idx, "outstanding"), outstanding, quote)
        )

        if not period_seen:
            pm = _PERIOD_RE.search(period)
            if pm:
                facts.append(
                    _make_fact(doc, P.RATES_WATER_PERIOD_FROM,
                               f"{pm.group(1)}-{pm.group(2)}-{pm.group(3)}", quote)
                )
                facts.append(
                    _make_fact(doc, P.RATES_WATER_PERIOD_TO,
                               f"{pm.group(4)}-{pm.group(5)}-{pm.group(6)}", quote)
                )
                period_days_val = _period_days(period)
                period_seen = True

    facts.append(
        _make_fact(
            doc, P.RATES_WATER_CHARGE_COUNT, len(matches),
            quote=f"{len(matches)} charge lines parsed",
            confidence=0.99,
        )
    )

    # Compute and emit the sum of current-period charge amounts (excluding outstanding)
    # and annualise based on detected billing period.
    try:
        current_period_total = sum(
            _parse_dollar(m.group("amount")) for m in matches
        )
        period_label, multiplier = _billing_multiplier(period_days_val)
        annual_amount = round(current_period_total * multiplier, 2)
        period_total_str = f"${current_period_total:,.2f}"
        annual_str = f"${annual_amount:,.2f}"

        quote_summary = (
            f"Sum of {len(matches)} charge-line amounts = {period_total_str} "
            f"({period_label}) × {multiplier} = {annual_str}"
        )

        facts.append(
            _make_fact(
                doc, P.RATES_WATER_CERT_PERIOD_TYPE, period_label,
                quote=f"period length ≈ {period_days_val} days → {period_label}",
                confidence=0.97,
                notes="derived from charge-line date range",
            )
        )

        # High-confidence cert annual amount (current-period charges only, no outstanding)
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str,
                quote=quote_summary,
                confidence=0.97,
                notes=(
                    f"{period_label} charge {period_total_str} × {multiplier} = {annual_str}. "
                    "Excludes outstanding/overdue amounts from previous periods."
                ),
            )
        )

        # Write to the shared annual_amount path. Authority cert wins over vendor
        # form for this path (authority rule: water_cert > vendor_form).
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_ANNUAL, annual_str,
                quote=quote_summary,
                confidence=0.95,
                notes=(
                    f"Annual water rate from cert: {period_label} {period_total_str} × {multiplier}. "
                    "Current-period charges only — outstanding debt excluded."
                ),
            )
        )
    except (ValueError, AttributeError):
        pass

    return facts


def _extract_charges_flex(doc: Document, text: str) -> list[Fact]:
    """Flexible charge-line extractor for water authorities that use:
      - DD/MM/YYYY slash dates (South East Water, some SEW statements)
      - No outstanding column
      - Label  FromDate  ToDate  $amount  format

    Falls back to _TOTAL_CURRENT_RE if no charge lines are found.
    """
    facts: list[Fact] = []

    matches = list(_CHARGE_LINE_FLEX_RE.finditer(text))
    if matches:
        period_days_val: int | None = None
        for m in matches:
            period_days_val = _period_days_flex(m.group("from"), m.group("to"))
            if period_days_val:
                break

        try:
            current_period_total = sum(
                _parse_dollar(m.group("amount")) for m in matches
            )
            period_label, multiplier = _billing_multiplier(period_days_val)
            annual_amount = round(current_period_total * multiplier, 2)
            period_total_str = f"${current_period_total:,.2f}"
            annual_str = f"${annual_amount:,.2f}"
            quote_summary = (
                f"Flex charge lines: {len(matches)} items = {period_total_str} "
                f"({period_label}) × {multiplier} = {annual_str}"
            )

            facts.append(
                _make_fact(
                    doc, P.RATES_WATER_CERT_PERIOD_TYPE, period_label,
                    quote=f"period ≈ {period_days_val} days → {period_label}",
                    confidence=0.94,
                    notes="derived from flexible charge-line date range",
                )
            )
            facts.append(
                _make_fact(
                    doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str,
                    quote=quote_summary,
                    confidence=0.93,
                    notes=f"{period_label} {period_total_str} × {multiplier} = {annual_str}",
                )
            )
            facts.append(
                _make_fact(
                    doc, P.RATES_WATER_ANNUAL, annual_str,
                    quote=quote_summary,
                    confidence=0.92,
                    notes=(
                        f"Annual water rate (flexible format): "
                        f"{period_label} {period_total_str} × {multiplier} = {annual_str}."
                    ),
                )
            )
            return facts
        except (ValueError, AttributeError):
            pass

    # No charge lines matched — try "Total current charges $X" / "Amount due $X"
    m = _TOTAL_CURRENT_RE.search(text)
    if m:
        total_str_raw = m.group(1)
        try:
            total_val = _parse_dollar(total_str_raw)
            if total_val <= 0:
                return facts
            # Find a period in the document to determine multiplier
            period_m = _PERIOD_FLEX_RE.search(text)
            period_days_val = None
            if period_m:
                period_days_val = _period_days_flex(
                    f"{period_m.group(1)}/{period_m.group(2)}/{period_m.group(3)}",
                    f"{period_m.group(4)}/{period_m.group(5)}/{period_m.group(6)}",
                )
            period_label, multiplier = _billing_multiplier(period_days_val)
            annual_amount = round(total_val * multiplier, 2)
            annual_str = f"${annual_amount:,.2f}"
            period_total_str = f"${total_val:,.2f}"
            quote_summary = (
                f"Total current charges {period_total_str} "
                f"({period_label}) × {multiplier} = {annual_str}"
            )
            facts.append(
                _make_fact(
                    doc, P.RATES_WATER_ANNUAL, annual_str,
                    quote=quote_summary,
                    confidence=0.88,
                    notes=(
                        f"Annual water rate from 'total current charges': "
                        f"{period_label} {period_total_str} × {multiplier} = {annual_str}."
                    ),
                )
            )
        except (ValueError, AttributeError):
            pass

    return facts


def _extract_explicit_annual(doc: Document, text: str) -> list[Fact]:
    """Extract annual amount when the certificate states it explicitly.

    Greater Western Water (and some other authorities) show a table with
    an 'Annual charge FY 20XX-XX' column and a 'Total annual charges $X'
    summary row. This is authoritative — use it directly.
    """
    facts: list[Fact] = []

    # 1. "Total annual charges $737.01" — authoritative annual total
    m = _TOTAL_ANNUAL_RE.search(text)
    if m:
        annual_str = m.group(1)
        quote = m.group(0).strip()
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_ANNUAL, annual_str, quote,
                confidence=0.99,
                notes=(
                    "Annual water rate from 'Total annual charges' row — "
                    "this is the authority's own annual figure, not a calculated estimate."
                ),
            )
        )
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str, quote,
                confidence=0.99,
                notes="Water cert explicit annual total (not annualised from quarterly).",
            )
        )
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_CERT_PERIOD_TYPE, "annual", quote,
                confidence=0.99,
                notes="Document shows explicit annual charges table.",
            )
        )
        return facts

    # 2. Broader "annual charges/amount" label fallback
    m = _ANNUAL_RE.search(text)
    if m:
        annual_str = m.group(1)
        quote = m.group(0).strip()
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_ANNUAL, annual_str, quote,
                confidence=0.95,
                notes="Annual water rate from explicit annual label in document.",
            )
        )
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str, quote,
                confidence=0.95,
            )
        )
        return facts

    # 3. GWW per-line format: sum the annual_charge column
    gww_matches = list(_GWW_ANNUAL_LINE_RE.finditer(text))
    if gww_matches:
        try:
            total = sum(_parse_dollar(m.group("annual")) for m in gww_matches)
            annual_str = f"${total:,.2f}"
            quote = f"Sum of {len(gww_matches)} annual charge lines = {annual_str}"
            facts.append(
                _make_fact(
                    doc, P.RATES_WATER_ANNUAL, annual_str, quote,
                    confidence=0.95,
                    notes=(
                        f"Annual water rate: sum of {len(gww_matches)} annual charge "
                        f"column amounts = {annual_str}. Frequency column confirms each is annual."
                    ),
                )
            )
            facts.append(
                _make_fact(
                    doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str, quote,
                    confidence=0.95,
                )
            )
        except (ValueError, AttributeError):
            pass

    return facts


def _extract_outstanding(doc: Document, text: str) -> list[Fact]:
    """Extract total outstanding (for disclosure, not for annual rate calculation)."""
    m = _TOTAL_RE.search(text)
    if not m:
        m = _TOTAL_ALT_RE.search(text)
    if not m:
        return []
    total_str = m.group(1)
    return [
        _make_fact(
            doc, P.RATES_WATER_TOTAL_OUTSTANDING, total_str, m.group(0),
            notes=(
                "'Total for This Property' / 'Total amount' includes current charges plus any "
                "overdue amounts — stored for disclosure only, not used as annual rate."
            ),
        ),
    ]


def _extract_doc_date(doc: Document, text: str) -> list[Fact]:
    m = _DATE_OF_ISSUE_RE.search(text)
    if not m:
        return []
    day, month, year = m.group(1), m.group(2), m.group(3)
    date_str = f"{int(day):02d}/{_month_to_num(month)}/{year}"
    return [
        _make_fact(
            doc,
            P.DOCS_WATER_CERT_DATE,
            date_str,
            _compact(m.group(0)),
            confidence=0.95,
            notes="water authority statement issue date",
        )
    ]


def _extract_services(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    if "Water Service Charge" in text:
        facts.append(
            _make_fact(
                doc,
                P.service_connected("water"),
                True,
                quote="Residential Water Service Charge",
                confidence=0.95,
                notes="implied by water authority charging a service fee",
            )
        )
    if "Sewer Service Charge" in text or "Sewerage" in text or "Wastewater" in text:
        facts.append(
            _make_fact(
                doc,
                P.service_connected("sewerage"),
                True,
                quote="Sewer/Wastewater Service Charge",
                confidence=0.95,
                notes="implied by water authority charging a sewer/wastewater fee",
            )
        )
    return facts


def _extract_daily_rate_annual(doc: Document, text: str) -> list[Fact]:
    """Handle daily rate lines (Central Highlands Water = $/day, Goulburn Valley = ¢/day).

    Format: "Label: From DD/MM/YYYY To DD/MM/YYYY = N Days @ R[¢] Per Day = $T"

    Annual = sum(daily_rate_in_dollars) × 365/366.
    Uses 4+ decimal precision; no intermediate rounding.
    """
    matches = list(_DAILY_RATE_LINE_RE.finditer(text))
    if not matches:
        return []

    facts: list[Fact] = []
    total_daily_dollars = 0.0
    labels: list[str] = []
    for m in matches:
        try:
            daily_raw = float(m.group("daily"))
            unit = (m.group("unit") or "").lower().strip()
            # cents unit → divide by 100 to get dollars/day
            daily_dollars = daily_raw / 100.0 if unit in ("¢", "c") else daily_raw
            total_daily_dollars += daily_dollars
            unit_label = f"{daily_raw:.4f}¢/day" if unit in ("¢", "c") else f"${daily_dollars:.4f}/day"
            labels.append(f"{m.group('label').strip()} @{unit_label}")
        except ValueError:
            pass

    if total_daily_dollars <= 0:
        return []

    # Determine days-in-year using first date found; use 366 for leap years.
    year_m = re.search(r"From\s+\d{1,2}/\d{2}/(\d{4})", text, re.IGNORECASE)
    if year_m:
        doc_year = int(year_m.group(1))
        days_in_year = 366 if calendar.isleap(doc_year) else 365
    else:
        days_in_year = 365
        doc_year = None

    annual = total_daily_dollars * days_in_year  # full precision
    annual_str = f"${annual:,.2f}"
    leap_note = f" (leap year {doc_year})" if days_in_year == 366 else ""
    quote = (
        f"Daily rates: {', '.join(labels)} → ${total_daily_dollars:.6f}/day "
        f"× {days_in_year}{leap_note} = {annual_str}"
    )

    facts.append(
        _make_fact(
            doc, P.RATES_WATER_ANNUAL, annual_str, quote,
            confidence=0.80,
            notes=(
                f"Annual water rate from daily rates: "
                f"${total_daily_dollars:.6f}/day × {days_in_year}{leap_note} = {annual_str}. "
                "NEEDS REVIEW — confirm against authority's published annual tariff."
            ),
        )
    )
    facts.append(
        _make_fact(
            doc, "rates.water.copy_rules_recommended", True,
            quote,
            confidence=0.95,
            notes="Daily-rate billing detected. DB copy rule lookup recommended.",
        )
    )
    return facts


def _extract_gippsland_annual(doc: Document, text: str) -> list[Fact]:
    """Handle Gippsland Water financial statements.

    Gippsland bills three times per year and the annual figure used for
    outgoings should come from the fixed service-charge lines, not usage /
    notional / debt / outstanding lines.
    """
    if "Gippsland Water" not in text or not _GIPPSLAND_BILLING_PERIODS_RE.search(text):
        return []

    fixed_matches = list(_GIPPSLAND_FIXED_SERVICE_RE.finditer(text))
    if fixed_matches:
        included: list[tuple[str, float]] = []
        for match in fixed_matches:
            label = _compact(match.group(1))
            try:
                value = float(match.group(2).replace(",", "").strip())
            except ValueError:
                continue
            if value > 0:
                included.append((label, value))
        if included:
            current_period_total = round(sum(value for _, value in included), 2)
            annual_amount = round(current_period_total * 3, 2)
            annual_str = f"${annual_amount:,.2f}"
            period_str = f"${current_period_total:,.2f}"
            parts = ", ".join(f"{label} ${value:,.2f}" for label, value in included)
            quote = f"Gippsland fixed service charges: {parts} → {period_str} × 3 = {annual_str}"
            return [
                _make_fact(
                    doc, P.RATES_WATER_CERT_PERIOD_TYPE, "tri_annual", quote,
                    confidence=0.96,
                    notes="Gippsland Water bills three times per year.",
                ),
                _make_fact(
                    doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str, quote,
                    confidence=0.96,
                    notes=(
                        f"Gippsland fixed service charges {period_str} × 3 = {annual_str}. "
                        "Usage / notional / debt lines excluded."
                    ),
                ),
                _make_fact(
                    doc, P.RATES_WATER_ANNUAL, annual_str, quote,
                    confidence=0.95,
                    notes=(
                        f"Annual Gippsland Water rate from fixed service charges only: "
                        f"{period_str} × 3 = {annual_str}."
                    ),
                ),
            ]

    block_match = _GIPPSLAND_CHARGE_BLOCK_RE.search(text)
    if block_match:
        block = block_match.group(1)
        raw_amounts = re.findall(r"(?<!\d)(-?\d[\d,]*\.\d{2})(?!\d)", block)
        positives: list[float] = []
        for raw in raw_amounts:
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if value > 0:
                positives.append(value)
        if positives:
            current_period_total = positives[0]
            annual_amount = round(current_period_total * 3, 2)
            annual_str = f"${annual_amount:,.2f}"
            period_str = f"${current_period_total:,.2f}"
            quote = f"Gippsland first positive fixed service amount {period_str} × 3 = {annual_str}"
            return [
                _make_fact(
                    doc, P.RATES_WATER_CERT_PERIOD_TYPE, "tri_annual", quote,
                    confidence=0.92,
                    notes="Gippsland Water bills three times per year.",
                ),
                _make_fact(
                    doc, P.RATES_WATER_ANNUAL, annual_str, quote,
                    confidence=0.92,
                    notes=(
                        f"Annual Gippsland Water rate from first positive fixed service charge: "
                        f"{period_str} × 3 = {annual_str}."
                    ),
                ),
            ]

    service_match = _GIPPSLAND_SERVICE_CHARGE_RE.search(text)
    if service_match:
        try:
            value = float(service_match.group(1).replace(",", "").strip())
        except ValueError:
            return []
        if value > 0:
            annual_amount = round(value * 3, 2)
            annual_str = f"${annual_amount:,.2f}"
            period_str = f"${value:,.2f}"
            quote = f"Water Service Charges {period_str} × 3 = {annual_str}"
            return [
                _make_fact(
                    doc, P.RATES_WATER_ANNUAL, annual_str, quote,
                    confidence=0.94,
                    notes="Annual Gippsland Water rate from tri-annual service charge.",
                )
            ]

    return []


def _extract_goulburn_valley_annual(doc: Document, text: str) -> list[Fact]:
    """Handle Goulburn Valley Water cents-per-day format.

    Fixed charges: label  X.XX¢/day  → annualise by × 365/366
    Variable usage: label  NkL @ X.XX¢/kL  within a billing period of D days
                           → (volume / days) × 365/366 × rate_in_dollars

    Uses 4+ decimal precision throughout to avoid intermediate rounding.
    """
    cents_matches = list(_CENTS_PER_DAY_RE.finditer(text))
    if not cents_matches:
        return []

    # Determine year from billing period or date of issue for leap-year check
    year_m = _BILLING_PERIOD_RE.search(text) or re.search(r"/(\d{4})", text)
    doc_year = None
    if year_m:
        try:
            doc_year = int(year_m.group(year_m.lastindex or 1)[-4:])
        except (ValueError, IndexError):
            pass
    days_in_year = 366 if (doc_year and calendar.isleap(doc_year)) else 365

    # Fixed cents-per-day charges
    fixed_total_cents = 0.0
    fixed_parts: list[str] = []
    for m in cents_matches:
        try:
            cents = float(m.group("cents"))
            fixed_total_cents += cents
            fixed_parts.append(f"{m.group('label').strip()} {cents:.4f}¢/day")
        except ValueError:
            pass

    if fixed_total_cents <= 0:
        return []

    # Billing period length (for usage pro-rata)
    billing_period_days: int | None = None
    bp_m = _BILLING_PERIOD_RE.search(text)
    if bp_m:
        billing_period_days = _period_days_flex(bp_m.group(1), bp_m.group(2))
    if not billing_period_days:
        # Try first date pair in the document
        period_m = _PERIOD_FLEX_RE.search(text)
        if period_m:
            billing_period_days = _period_days_flex(
                f"{period_m.group(1)}/{period_m.group(2)}/{period_m.group(3)}",
                f"{period_m.group(4)}/{period_m.group(5)}/{period_m.group(6)}",
            )

    # Variable usage lines
    usage_annual_cents = 0.0
    usage_parts: list[str] = []
    for m in _USAGE_KL_RE.finditer(text):
        try:
            volume_kl = float(m.group("volume"))
            rate_cents_per_kl = float(m.group("rate"))
            period_d = billing_period_days or 91  # default quarterly
            # annualise: (volume / period_days) * days_in_year * rate
            annual_kl = (volume_kl / period_d) * days_in_year
            annual_cents = annual_kl * rate_cents_per_kl
            usage_annual_cents += annual_cents
            usage_parts.append(
                f"{m.group('label').strip()} ({volume_kl}kL/{period_d}days×{days_in_year}×{rate_cents_per_kl:.4f}¢/kL={annual_cents:.4f}¢)"
            )
        except (ValueError, ZeroDivisionError):
            pass

    # Convert cents to dollars with full precision
    total_annual_cents = fixed_total_cents * days_in_year + usage_annual_cents
    annual_dollars = total_annual_cents / 100.0
    annual_str = f"${annual_dollars:,.2f}"

    leap_note = f" (leap year {doc_year})" if days_in_year == 366 else ""
    parts_desc = "; ".join(fixed_parts)
    if usage_parts:
        parts_desc += "; " + "; ".join(usage_parts)
    quote = (
        f"Goulburn Valley cents/day: fixed {fixed_total_cents:.4f}¢/day × {days_in_year}{leap_note}"
        + (f" + usage {usage_annual_cents:.4f}¢/year" if usage_annual_cents else "")
        + f" = {total_annual_cents:.4f}¢ = {annual_str}"
    )

    return [
        _make_fact(
            doc, P.RATES_WATER_CERT_PERIOD_TYPE, "annual", quote,
            confidence=0.93,
            notes="Goulburn Valley Water cents/day billing — annualised by ×365/366.",
        ),
        _make_fact(
            doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str, quote,
            confidence=0.93,
            notes=f"Goulburn Valley Water: {parts_desc}",
        ),
        _make_fact(
            doc, P.RATES_WATER_ANNUAL, annual_str, quote,
            confidence=0.82,
            notes=(
                f"Annual water rate calculated from cents/day charges{leap_note}: {parts_desc}. "
                "NEEDS REVIEW — confirm against authority's published annual tariff."
            ),
        ),
        _make_fact(
            doc, "rates.water.needs_review", True, quote,
            confidence=0.99,
            notes="Calculated from daily/volume rates. Verify against authority's published annual tariff.",
        ),
    ]


def _extract_charges_per_row(doc: Document, text: str) -> list[Fact]:
    """Per-row period detection for mixed annual/quarterly statements (e.g. Westernport Water).

    Each charge line carries its own date range and gets its own multiplier.
    Annual charge = sum(amount_i × multiplier_i for each row).

    Falls back to the simpler sum-with-shared-multiplier if all rows share the same period.
    """
    matches = list(_CHARGE_LINE_FLEX_RE.finditer(text))
    if not matches:
        return []

    row_annuals: list[tuple[str, float, int, str]] = []  # label, annual, multiplier, period_type
    for m in matches:
        try:
            amount = _parse_dollar(m.group("amount"))
            days = _period_days_flex(m.group("from"), m.group("to"))
            period_label, multiplier = _billing_multiplier(days)
            annual = amount * multiplier
            row_annuals.append((
                _compact(m.group("label")),
                annual,
                multiplier,
                period_label,
            ))
        except (ValueError, AttributeError):
            continue

    if not row_annuals:
        return []

    # Check if all rows share the same multiplier — if so, simpler note
    multipliers = {m for _, _, m, _ in row_annuals}
    mixed = len(multipliers) > 1

    total_annual = sum(a for _, a, _, _ in row_annuals)
    annual_str = f"${total_annual:,.2f}"
    row_desc = "; ".join(
        f"{lbl} ${a:,.2f}(×{mult})" for lbl, a, mult, _ in row_annuals
    )
    quote = f"Per-row annualised: {row_desc} = {annual_str}"

    facts = [
        _make_fact(
            doc, P.RATES_WATER_CERT_PERIOD_TYPE, "mixed" if mixed else row_annuals[0][3],
            quote, confidence=0.92,
            notes="Period type per charge row.",
        ),
        _make_fact(
            doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str, quote,
            confidence=0.92,
            notes=f"Per-row annualised sum: {row_desc}",
        ),
        _make_fact(
            doc, P.RATES_WATER_ANNUAL, annual_str, quote,
            confidence=0.82,
            notes=(
                f"Annual water rate from per-row period detection: {row_desc}. "
                "NEEDS REVIEW — mixed billing periods detected." if mixed else
                f"Annual water rate (per-row, {row_annuals[0][3]}): {row_desc}."
            ),
        ),
    ]
    if mixed:
        facts.append(
            _make_fact(
                doc, "rates.water.needs_review", True, quote,
                confidence=0.99,
                notes="Mixed billing periods (annual+quarterly rows). Verify calculation.",
            )
        )
    return facts


def _water_debug_log(
    doc: Document,
    authority: str | None,
    method: str,
    annual_str: str | None,
    all_facts: list[Fact],
) -> None:
    """Print a structured debug block for this water document."""
    fname = doc.filename or "unknown"
    print(f"\n  +- WATER DEBUG [{fname}]")
    print(f"  |  Authority detected : {authority or '(none)'}")
    print(f"  |  Extraction method  : {method}")
    print(f"  |  Annual amount      : {annual_str or '(not found)'}")
    if all_facts:
        annual_facts = [f for f in all_facts if f.path == P.RATES_WATER_ANNUAL]
        if annual_facts:
            print(f"  |  Facts emitted ({P.RATES_WATER_ANNUAL}):")
            for f in annual_facts:
                print(f"  |    conf={f.confidence:.2f}  value={f.value!r}")
                if f.notes:
                    print(f"  |    note: {f.notes[:120]}")
    cr_facts = [f for f in all_facts if "copy_rules" in f.path]
    if cr_facts:
        print(f"  |  ! Copy rules recommended -- database lookup needed")
    print(f"  +---------------------------------------------")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_water_authority_certificate_facts(doc: Document) -> list[Fact]:
    """Extract facts from a Victorian water authority information statement."""
    text = _doc_text(doc)
    if not text:
        return []
    _WATER_TRIGGERS = (
        "Water Information Statement",
        "Rate Certificate",
        "Information Statement Certificate",
        "Water Rates",
        "Rates Certificate",
        "Water Act 1989",          # Central Highlands / generic
        "Section 158",             # Water Act citation used by all VIC water corps
    )
    if not any(t in text for t in _WATER_TRIGGERS):
        return []

    facts: list[Fact] = []
    auth_facts = _extract_authority(doc, text)
    facts.extend(auth_facts)
    authority_name = auth_facts[0].value if auth_facts else None

    facts.extend(_extract_certificate_number(doc, text))
    facts.extend(_extract_property_row(doc, text))

    # --- Annual amount: try each method in priority order ---
    method = "none"
    annual_str = None

    explicit_facts = _extract_explicit_annual(doc, text)
    if explicit_facts:
        facts.extend(explicit_facts)
        annual_facts = [f for f in explicit_facts if f.path == P.RATES_WATER_ANNUAL]
        if annual_facts:
            method = "explicit_annual_label"
            annual_str = str(annual_facts[0].value)

    if method == "none":
        charge_facts = _extract_charges(doc, text)
        if charge_facts:
            facts.extend(charge_facts)
            annual_facts = [f for f in charge_facts if f.path == P.RATES_WATER_ANNUAL]
            if annual_facts:
                method = "quarterly_charge_lines (hyphen-date)"
                annual_str = str(annual_facts[0].value)

    # Flexible charge lines: slash-date format (South East Water, etc.)
    if method == "none":
        flex_facts = _extract_charges_flex(doc, text)
        if flex_facts:
            facts.extend(flex_facts)
            annual_facts = [f for f in flex_facts if f.path == P.RATES_WATER_ANNUAL]
            if annual_facts:
                method = "charge_lines_flex (slash-date)"
                annual_str = str(annual_facts[0].value)

    if method == "none":
        gippsland_facts = _extract_gippsland_annual(doc, text)
        if gippsland_facts:
            facts.extend(gippsland_facts)
            annual_facts = [f for f in gippsland_facts if f.path == P.RATES_WATER_ANNUAL]
            if annual_facts:
                method = "gippsland_fixed_service_x3"
                annual_str = str(annual_facts[0].value)

    if method == "none":
        goulburn_facts = _extract_goulburn_valley_annual(doc, text)
        if goulburn_facts:
            facts.extend(goulburn_facts)
            annual_facts = [f for f in goulburn_facts if f.path == P.RATES_WATER_ANNUAL]
            if annual_facts:
                method = "goulburn_valley_cents_per_day"
                annual_str = str(annual_facts[0].value)

    if method == "none":
        per_row_facts = _extract_charges_per_row(doc, text)
        if per_row_facts:
            facts.extend(per_row_facts)
            annual_facts = [f for f in per_row_facts if f.path == P.RATES_WATER_ANNUAL]
            if annual_facts:
                method = "per_row_period_detection"
                annual_str = str(annual_facts[0].value)

    if method == "none":
        daily_facts = _extract_daily_rate_annual(doc, text)
        if daily_facts:
            facts.extend(daily_facts)
            # Mark as needs_review since this is a calculated amount
            facts.append(
                _make_fact(
                    doc, "rates.water.needs_review", True,
                    "daily rate calculation",
                    confidence=0.99,
                    notes="Daily-rate calculation. Confirm against authority's published annual tariff.",
                )
            )
            annual_facts = [f for f in daily_facts if f.path == P.RATES_WATER_ANNUAL]
            if annual_facts:
                method = "daily_rate_x365"
                annual_str = str(annual_facts[0].value)

    facts.extend(_extract_outstanding(doc, text))
    facts.extend(_extract_doc_date(doc, text))
    facts.extend(_extract_services(doc, text))

    _water_debug_log(doc, authority_name, method, annual_str, facts)

    return facts
