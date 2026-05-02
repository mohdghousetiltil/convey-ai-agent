"""Land Tax Property Clearance Certificate extractor (Victorian SRO).

Reads a Property Clearance Certificate (Land Tax) PDF issued by the
Victorian State Revenue Office and emits Facts at canonical
rates.land_tax.* paths.

The PDF text layout is awkward (form fields and labels are not aligned
linearly with their values when extracted by pypdf), so this extractor
uses substring anchors rather than left-to-right scanning.

Registered as `rule:land_tax_certificate_v1` so it matches the
`rule:land_tax_certificate*` glob in DEFAULT_AUTHORITY_RULES.
"""
from __future__ import annotations

import re

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:land_tax_certificate_v1"


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


def _amount_to_zero_bool(value: str) -> bool:
    """Return True iff the dollar string represents zero."""
    if value is None:
        return False
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned) == 0.0
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_CERT_NO_RE = re.compile(r"Certificate No:?\s*\n?\s*(\d{6,})", re.IGNORECASE)
_LAND_ADDRESS_RE = re.compile(r"Land Address:\s*(.+?)(?:\r?\n|$)", re.IGNORECASE)
_VENDOR_RE = re.compile(r"Vendor:\s*\n?\s*(.+?)(?:\r?\n)", re.IGNORECASE)
_LAND_ID_RE = re.compile(r"Land Id\s*\n\s*(\d+)", re.IGNORECASE)
# Matches both complete and incomplete Volume/Folio rows.
# Groups: (lot, plan, volume_or_None, folio_or_None)
_LOT_PLAN_VF_RE = re.compile(
    r"Lot\s+Plan\s+Volume\s+Folio\s*\n\s*(\d+)\s+(\w+)(?:\s+(\d+)\s+(\d+))?",
    re.IGNORECASE,
)
_TAX_PAYABLE_RE = re.compile(
    r"Tax Payable\s*\n\s*(\$[\d,]+\.\d{2})", re.IGNORECASE
)
# Matches "Land Tax = $1,515.00" in the "For Information Only" calculation section.
# This is always preferred over Tax Payable = $0.00 because the certificate may show
# $0 when Volume/Folio is incomplete or the property has a PPoR exemption — the
# calculation figure is the true single-ownership land tax amount.
_LAND_TAX_VALUE_RE = re.compile(
    r"\bLand\s+Tax\s*=\s*\$\s*([\d,]+\.\d{2})",
    re.IGNORECASE | re.MULTILINE,
)
_SITE_VALUE_RE = re.compile(
    r"Taxable Value \(SV\)\s*\n\s*(\$[\d,]+)", re.IGNORECASE
)
_CIV_RE = re.compile(
    r"(\$[\d,]+)\s*\n?\s*CAPITAL IMPROVED VALUE", re.IGNORECASE
)
_EXEMPTION_RE = re.compile(
    r"Property is exempt:\s*(.+?)(?:\r?\n|$)", re.IGNORECASE
)
_COMMISSIONER_RE = re.compile(
    r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s*\n\s*Property Clearance Certificate", re.IGNORECASE
)
_ISSUE_DATE_RE = re.compile(
    r"\b(\d{1,2}\s+[A-Z]{3}\s+\d{4})\b", re.IGNORECASE
)
_YEAR_LINE_RE = re.compile(r"\$0\.00\s*(\d{4})\b")


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------


def _extract_certificate_meta(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    if m := _CERT_NO_RE.search(text):
        facts.append(
            _make_fact(doc, P.RATES_LAND_TAX_CERTIFICATE_NUMBER, m.group(1), m.group(0))
        )
    if m := _ISSUE_DATE_RE.search(text):
        # The first date in this PDF is the issue date.
        issue_date_raw = m.group(1)
        facts.append(
            _make_fact(
                doc,
                "rates.land_tax.issue_date",
                issue_date_raw,
                m.group(0),
                confidence=0.92,
                notes="first date pattern in document",
            )
        )
        # Normalise to DD/MM/YYYY for the attachment list builder
        try:
            from datetime import datetime
            dt = datetime.strptime(issue_date_raw, "%d %b %Y")
            date_str = dt.strftime("%d/%m/%Y")
        except ValueError:
            date_str = issue_date_raw
        facts.append(
            _make_fact(doc, P.DOCS_LAND_TAX_CERT_DATE, date_str, m.group(0),
                       confidence=0.97, notes="SRO certificate issue date")
        )
    return facts


def _extract_land_identity(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    if m := _LAND_ADDRESS_RE.search(text):
        addr = _compact(m.group(1))
        quote = _compact(m.group(0))
        facts.append(
            _make_fact(doc, P.PROPERTY_ADDRESS, addr, quote, confidence=0.93,
                       notes="from SRO land tax certificate")
        )
    if m := _LAND_ID_RE.search(text):
        facts.append(
            _make_fact(doc, "rates.land_tax.land_id", m.group(1), _compact(m.group(0)))
        )
    if m := _LOT_PLAN_VF_RE.search(text):
        lot, plan, volume, folio = m.group(1), m.group(2), m.group(3), m.group(4)
        quote = _compact(m.group(0))
        facts.append(
            _make_fact(doc, P.PROPERTY_LOT_NUMBER, lot, quote, confidence=0.95)
        )
        facts.append(
            _make_fact(doc, P.PROPERTY_PLAN_NUMBER, plan, quote, confidence=0.95)
        )
        if volume:
            facts.append(
                _make_fact(doc, P.PROPERTY_VOLUME_OR_BOOK, volume, quote, confidence=0.95)
            )
        if folio:
            facts.append(
                _make_fact(doc, P.PROPERTY_FOLIO_OR_NUMBER, folio, quote, confidence=0.95)
            )
    return facts


def _volume_folio_complete(text: str) -> bool:
    """Return True if both Volume and Folio are populated on the certificate."""
    m = _LOT_PLAN_VF_RE.search(text)
    if not m:
        return False
    return bool(m.group(3) and m.group(4))


def _has_property_exemption(text: str) -> bool:
    return _EXEMPTION_RE.search(text) is not None


def _extract_tax_amounts(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    tax_amount: str | None = None
    tax_quote: str | None = None

    vf_complete = _volume_folio_complete(text)
    payable_amount: str | None = None
    payable_quote: str | None = None
    if m := _TAX_PAYABLE_RE.search(text):
        payable_amount = m.group(1)
        payable_quote = _compact(m.group(0))

    # Rule 1: Volume AND Folio both present + Tax Payable = $0 → operative amount is $0.00
    # (property is finalised / exempt — no land tax applies)
    vf_zero = (
        vf_complete
        and payable_amount is not None
        and _amount_to_zero_bool(payable_amount)
    )

    if vf_zero:
        tax_amount = "$0.00"
        tax_quote = payable_quote or "Tax Payable $0.00 — Volume/Folio present"
        facts.append(
            _make_fact(
                doc,
                P.RATES_LAND_TAX_AMOUNT,
                tax_amount,
                tax_quote,
                confidence=0.99,
                notes=(
                    "Volume and Folio both present AND Tax Payable = $0.00. "
                    "Certificate is finalised — operative land tax = $0.00."
                ),
            )
        )
    else:
        # Rule 2: Volume/Folio missing  → use 'Land Tax = $X' calculation figure.
        # Rule 3: Volume/Folio present + Tax Payable ≠ $0 → also use calculation figure.
        # In both cases the 'For Information Only' single-ownership amount is authoritative.
        if m := _LAND_TAX_VALUE_RE.search(text):
            tax_amount = "$" + m.group(1)
            tax_quote = _compact(m.group(0))
            if not vf_complete:
                note = "Volume/Folio blank — certificate not finalised. Using single-ownership calculation."
            else:
                note = "Volume/Folio present but Tax Payable is non-zero. Using single-ownership calculation."
            facts.append(
                _make_fact(
                    doc,
                    P.RATES_LAND_TAX_AMOUNT,
                    tax_amount,
                    tax_quote,
                    confidence=0.99,
                    notes=note,
                )
            )

    if payable_amount is not None and payable_quote is not None:
        # Only use Tax Payable as the amount if nothing else resolved.
        if tax_amount is None:
            tax_amount = payable_amount
            tax_quote = payable_quote
            facts.append(
                _make_fact(
                    doc,
                    P.RATES_LAND_TAX_AMOUNT,
                    tax_amount,
                    payable_quote,
                    confidence=0.97,
                    notes="land tax amount sourced from Tax Payable field",
                )
            )
        effective_amount = tax_amount or payable_amount
        facts.append(
            _make_fact(
                doc,
                P.RATES_LAND_TAX_PAYABLE,
                not _amount_to_zero_bool(effective_amount),
                payable_quote,
                confidence=0.99,
                notes=(
                    f"derived from land tax amount {effective_amount}; "
                    f"Tax Payable field = {payable_amount}"
                ),
            )
        )
        facts.append(
            _make_fact(
                doc,
                P.RATES_VACANT_LAND_TAX_PAYABLE,
                not _amount_to_zero_bool(effective_amount),
                payable_quote,
                confidence=0.85,
                notes="follows the main land tax payable status on this certificate",
            )
        )
    elif tax_amount is not None and tax_quote is not None:
        facts.append(
            _make_fact(
                doc,
                P.RATES_LAND_TAX_PAYABLE,
                not _amount_to_zero_bool(tax_amount),
                tax_quote,
                confidence=0.95,
                notes="derived from direct land tax amount on SRO certificate",
            )
        )
        facts.append(
            _make_fact(
                doc,
                P.RATES_VACANT_LAND_TAX_PAYABLE,
                not _amount_to_zero_bool(tax_amount),
                tax_quote,
                confidence=0.80,
                notes="follows direct land tax amount when Tax Payable field is absent",
            )
        )

    if m := _SITE_VALUE_RE.search(text):
        facts.append(
            _make_fact(
                doc,
                P.RATES_LAND_TAX_TAXABLE_VALUE,
                m.group(1),
                _compact(m.group(0)),
            )
        )
        facts.append(
            _make_fact(
                doc,
                P.RATES_LAND_TAX_SITE_VALUE,
                m.group(1),
                _compact(m.group(0)),
            )
        )
    if m := _CIV_RE.search(text):
        facts.append(
            _make_fact(
                doc,
                P.RATES_LAND_TAX_CAPITAL_IMPROVED_VALUE,
                m.group(1),
                _compact(m.group(0)),
            )
        )
    if m := _EXEMPTION_RE.search(text):
        facts.append(
            _make_fact(
                doc,
                P.RATES_LAND_TAX_EXEMPTION_REASON,
                _compact(m.group(1)),
                _compact(m.group(0)),
            )
        )
    return facts


def _extract_year(doc: Document, text: str) -> list[Fact]:
    if m := _YEAR_LINE_RE.search(text):
        return [
            _make_fact(
                doc,
                P.RATES_LAND_TAX_ASSESSED_YEAR,
                m.group(1),
                _compact(m.group(0)),
                confidence=0.92,
            )
        ]
    return []


def _extract_vendor_and_commissioner(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    if m := _VENDOR_RE.search(text):
        facts.append(
            _make_fact(
                doc, P.RATES_LAND_TAX_VENDOR_NAME, _compact(m.group(1)), _compact(m.group(0))
            )
        )
    if m := _COMMISSIONER_RE.search(text):
        facts.append(
            _make_fact(
                doc, P.RATES_LAND_TAX_COMMISSIONER, _compact(m.group(1)), _compact(m.group(0))
            )
        )
    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _emit_authority_name(doc: Document, text: str) -> list[Fact]:
    """Land tax is always charged by the SRO — emit the authority name so
    Brain B can fill outgoing row 3 without needing it in the registry."""
    if "Property Clearance Certificate" not in text:
        return []
    return [
        _make_fact(
            doc,
            P.RATES_LAND_TAX_AUTHORITY_NAME,
            "State Revenue Office",
            "Property Clearance Certificate - Land Tax",
            confidence=0.99,
            notes="SRO is always the land tax authority in Victoria",
        )
    ]


def extract_land_tax_certificate_facts(doc: Document) -> list[Fact]:
    """Extract facts from an SRO Land Tax Property Clearance Certificate."""
    text = _doc_text(doc)
    if not text:
        return []
    if "Property Clearance Certificate" not in text or "Land Tax" not in text:
        return []

    facts: list[Fact] = []
    facts.extend(_extract_certificate_meta(doc, text))
    facts.extend(_extract_land_identity(doc, text))
    facts.extend(_extract_tax_amounts(doc, text))
    facts.extend(_extract_year(doc, text))
    facts.extend(_extract_vendor_and_commissioner(doc, text))
    facts.extend(_emit_authority_name(doc, text))
    return facts
