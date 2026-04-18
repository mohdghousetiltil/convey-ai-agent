"""Water authority certificate extractor (Yarra Valley Water et al.).

Reads a Section 158 Water Information Statement / Rates Certificate PDF
issued by a Victorian water authority and emits Facts at canonical
rates.water.* paths. Also confirms `services.water.connected` and
`services.sewerage.connected` because the certificate's existence
implies both are physically supplied.

Registered as `rule:water_authority_certificate_v1` so it matches the
`rule:water_authority_certificate*` glob in DEFAULT_AUTHORITY_RULES.

Annual amount extraction
------------------------
Victorian water authorities issue bills quarterly (every 3 months). To
get the annual amount we:
  1. Parse each individual charge line's `amount` field (current-period
     charge only — NOT the `outstanding` carry-forward column).
  2. Sum those amounts to get the true quarterly charge.
  3. Determine the billing period length from the date range on the first
     charge line (e.g. "01-04-2026 to 30-06-2026" → ~91 days → quarterly).
  4. Annualise: quarterly × 4, semi-annual × 2, annual × 1.

This is more accurate than using "Total for This Property" which includes
overdue debt from previous periods and would overstate the annual rate.
"""
from __future__ import annotations

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
    m = _PERIOD_RE.search(period_str)
    if not m:
        return None
    d1, mo1, y1, d2, mo2, y2 = [int(x) for x in m.groups()]
    try:
        start = date(y1, mo1, d1)
        end = date(y2, mo2, d2)
        return (end - start).days + 1
    except ValueError:
        return None


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
            # Find the actual cased version in text
            idx = text.lower().find(name.lower())
            actual = text[idx: idx + len(name)]
            return [
                _make_fact(
                    doc,
                    P.RATES_WATER_AUTHORITY,
                    actual,
                    quote=actual,
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
    if "Sewer Service Charge" in text or "Sewerage" in text:
        facts.append(
            _make_fact(
                doc,
                P.service_connected("sewerage"),
                True,
                quote="Residential Sewer Service Charge",
                confidence=0.95,
                notes="implied by water authority charging a sewer service fee",
            )
        )
    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_water_authority_certificate_facts(doc: Document) -> list[Fact]:
    """Extract facts from a Victorian water authority information statement."""
    text = _doc_text(doc)
    if not text:
        return []
    if (
        "Water Information Statement" not in text
        and "Rate Certificate" not in text
        and "Information Statement Certificate" not in text
        and "Water Rates" not in text
        and "Rates Certificate" not in text
    ):
        return []

    facts: list[Fact] = []
    facts.extend(_extract_authority(doc, text))
    facts.extend(_extract_certificate_number(doc, text))
    facts.extend(_extract_property_row(doc, text))
    facts.extend(_extract_explicit_annual(doc, text))   # explicit annual first
    facts.extend(_extract_charges(doc, text))            # charge-line format fallback
    facts.extend(_extract_outstanding(doc, text))
    facts.extend(_extract_doc_date(doc, text))
    facts.extend(_extract_services(doc, text))
    return facts
