"""Water authority certificate extractor (Yarra Valley Water et al.).

Reads a Section 158 Water Information Statement / Rates Certificate PDF
issued by a Victorian water authority and emits Facts at canonical
rates.water.* paths. Also confirms `services.water.connected` and
`services.sewerage.connected` because the certificate's existence
implies both are physically supplied.

Registered as `rule:water_authority_certificate_v1` so it matches the
`rule:water_authority_certificate*` glob in DEFAULT_AUTHORITY_RULES.
"""
from __future__ import annotations

import re

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:water_authority_certificate_v1"

# ---------------------------------------------------------------------------
# Anchors / regex
# ---------------------------------------------------------------------------

# The authority name is in the page header, e.g. "Yarra Valley Water".
# We detect a small set of known authorities by name to keep this safe.
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
)

# Certificate number — appears as "Information Statement <num>" or
# "Rate Certificate No: <num>".
_CERT_NO_RE = re.compile(
    r"(?:Information Statement|Rate Certificate No\.?:?|Certificate No\.?:?)\s*[:#]?\s*(\d{6,})",
    re.IGNORECASE,
)

# Property/account number from the rates table row.
# Header: "Property Address Lot & Plan Property Number Property Type"
# Row:    "<address>  <lot>\<plan> <propnum> Residential"
_PROPERTY_ROW_RE = re.compile(
    r"Property Address\s+Lot\s*&\s*Plan\s+Property Number\s+Property Type\s*\n"
    r"(?P<address>.+?)\s+(?P<lot>\d+)\\(?P<plan>[A-Z]{0,3}\d+[A-Z]?)\s+(?P<propnum>\d+)\s+(?P<ptype>\w+)",
    re.IGNORECASE,
)

# Each charge line in the rates table looks like:
#   "<label>  <period>  <amount>  <outstanding>"
# where period is like "01-04-2026 to 30-06-2026" and amounts are "$XX.XX"
_CHARGE_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z &/]+?)\s+"
    r"(?P<period>\d{2}-\d{2}-\d{4}\s+to\s+\d{2}-\d{2}-\d{4})\s+"
    r"(?P<amount>\$[\d,]+\.\d{2})\s+"
    r"(?P<outstanding>\$[\d,]+\.\d{2})\s*$",
    re.MULTILINE,
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

# Period at the top — captured from the first charge line.
_PERIOD_RE = re.compile(
    r"(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})"
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


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------


def _extract_authority(doc: Document, text: str) -> list[Fact]:
    for name in _KNOWN_AUTHORITIES:
        if name in text:
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
    facts: list[Fact] = []
    matches = list(_CHARGE_LINE_RE.finditer(text))
    if not matches:
        return facts

    period_seen = False
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
                    _make_fact(doc, P.RATES_WATER_PERIOD_FROM, pm.group(1), quote)
                )
                facts.append(
                    _make_fact(doc, P.RATES_WATER_PERIOD_TO, pm.group(2), quote)
                )
                period_seen = True

    facts.append(
        _make_fact(
            doc, P.RATES_WATER_CHARGE_COUNT, len(matches),
            quote=f"{len(matches)} charge lines parsed",
            confidence=0.99,
        )
    )
    return facts


def _extract_total(doc: Document, text: str) -> list[Fact]:
    m = _TOTAL_RE.search(text)
    if not m:
        m = _TOTAL_ALT_RE.search(text)
    if not m:
        return []
    total_str = m.group(1)
    quote = m.group(0)
    facts = [
        _make_fact(doc, P.RATES_WATER_TOTAL_OUTSTANDING, total_str, quote),
        _make_fact(doc, P.RATES_WATER_CERT_PERIOD_TYPE, "quarterly", quote,
                   confidence=0.97, notes="Water authority certs bill quarterly"),
    ]

    # Annualise: quarterly × 4 → write to the shared annual_amount path at low
    # confidence so the vendor-form value (confidence 0.97) wins whenever present.
    # If no vendor-form water rate exists, this estimate is used by Brain B.
    try:
        quarterly = float(total_str.replace("$", "").replace(",", ""))
        annual_est = round(quarterly * 4, 2)
        annual_str = f"${annual_est:,.2f}"
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_ANNUAL, annual_str, quote,
                confidence=0.75,
                notes=(
                    f"estimated annual = quarterly {total_str} × 4 = {annual_str}. "
                    "Vendor-form annual figure is preferred; verify discrepancy."
                ),
            )
        )
        facts.append(
            _make_fact(
                doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str, quote,
                confidence=0.97,
                notes=f"quarterly {total_str} × 4 = {annual_str}",
            )
        )
    except (ValueError, AttributeError):
        pass

    return facts


def _extract_doc_date(doc: Document, text: str) -> list[Fact]:
    """Use the statement issue date, not the billing period end date."""
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
    """The certificate's existence implies water + sewerage are connected."""
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
    ):
        return []

    facts: list[Fact] = []
    facts.extend(_extract_authority(doc, text))
    facts.extend(_extract_certificate_number(doc, text))
    facts.extend(_extract_property_row(doc, text))
    facts.extend(_extract_charges(doc, text))
    facts.extend(_extract_total(doc, text))
    facts.extend(_extract_doc_date(doc, text))
    facts.extend(_extract_services(doc, text))
    return facts
