"""Water authority certificate extractor (all Victorian water authorities).

Candidate-engine architecture
------------------------------
Every extraction strategy contributes ``WaterCandidate`` objects.
Candidates are ranked by confidence + source priority; the winner is
converted to Fact objects.

Priority order (highest -> lowest):
  1. explicit_annual       doc states "Total annual charges $X"       conf 0.99
  2. gww_annual_column     GWW per-line annual column sum             conf 0.95
  3. gippsland_x3          Gippsland fixed service charges × 3        conf 0.90–0.96
  4. period_sum_*          charge lines × period multiplier            conf 0.82–0.95
  5. total_due_annualised  "Total amount due" × multiplier            conf 0.75
  6. daily_rate_x365       sum(daily rates) × 365/366                 conf 0.80
  7. usage_estimate        volume × rate (variable — last resort)     conf 0.55

DB copy rules (injected by ``fact_extraction.py``) always override at conf 0.99.

Date formats supported everywhere:
  22/09/25  22/09/2025  22-09-2025  22 Sep 2025  29-Jan-2026
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.extractors.omni_perception import (
    annual_from_omni_rows,
    classify_table_with_omni,
    should_call_omni,
    validate_omni_candidate,
    write_debug_log as omni_write_debug_log,
)
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:water_authority_certificate_v1"

# ---------------------------------------------------------------------------
# Authority registry
# ---------------------------------------------------------------------------

# Canonical names emitted in facts — order matters (longer/more specific first).
_KNOWN_AUTHORITIES: tuple[str, ...] = (
    "Yarra Valley Water",
    "South East Water",
    "City West Water",
    "Greater Western Water",
    "Western Water",
    "Barwon Water",
    "Coliban Water",
    "Goulburn Valley Water",
    "Central Highlands Water",
    "East Gippsland Water",
    "Gippsland Water",
    "Lower Murray Water",
    "North East Water",
    "South Gippsland Water",
    "Wannon Water",
    "GWMWater",
    "Grampians Wimmera Mallee Water",
    "Westernport Water",
)

# Raw document text (lower-case) -> canonical name.
# Used when the PDF header wording differs from the canonical name.
_AUTHORITY_ALIASES: dict[str, str] = {
    "goulburn valley region water corporation": "Goulburn Valley Water",
    "central highlands region water corporation": "Central Highlands Water",
    "grampians wimmera mallee water corporation": "GWMWater",
    "greater western water corporation": "Greater Western Water",
    "greater west water": "Greater Western Water",
    "greater west water corporation": "Greater Western Water",
}

# ---------------------------------------------------------------------------
# Universal date building blocks
# ---------------------------------------------------------------------------

# Numeric date: DD/MM/YY, DD/MM/YYYY, DD-MM-YY, DD-MM-YYYY
_DATE_NUM_PAT = r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
# Alpha date: "29-Jan-2026", "22 Sep 25", "01 Jul 2025"
_DATE_ALPHA_PAT = r"\d{1,2}[\s\-][A-Za-z]{3,9}[\s\-]\d{2,4}"
# Combined
_DATE_ANY = rf"(?:{_DATE_NUM_PAT}|{_DATE_ALPHA_PAT})"

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_CERT_NO_RE = re.compile(
    r"(?:Information Statement|Rate Certificate No\.?:?|Certificate No\.?:?)\s*[:#]?\s*(\d{6,})",
    re.IGNORECASE,
)

_PROPERTY_ROW_RE = re.compile(
    r"Property Address\s+Lot\s*&\s*Plan\s+Property Number\s+Property Type\s*\n"
    r"(?P<address>.+?)\s+(?P<lot>\d+)\\(?P<plan>[A-Z]{0,3}\d+[A-Z]?)\s+(?P<propnum>\d+)\s+(?P<ptype>\w+)",
    re.IGNORECASE,
)

# "Total annual charges $737.01" — GWW / explicit authoritative annual total
_TOTAL_ANNUAL_RE = re.compile(
    r"Total\s+annual\s+charges?\s+\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Broader annual-label fallback
_ANNUAL_LABEL_RE = re.compile(
    r"(?:"
    r"annual\s+(?:rates?|charges?|amount|fees?|service\s+fees?)"
    r"|total\s+annual"
    r"|annual\s+total"
    r")"
    r"[^\n$]{0,50}\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

# GWW per-line annual column:
# "Residential Water Service Charge  $224.24  Quarterly  $168.33  $168.33"
_GWW_ANNUAL_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z\s&/]+?)\s+"
    r"\$(?P<annual>[\d,]+\.\d{2})\s+"
    r"(?P<freq>Quarterly|Half[\s-]?yearly|Annual|Monthly)\s+"
    r"\$(?P<ytd>[\d,]+\.\d{2})\s+"
    r"\$(?P<outstanding>[\d,]+\.\d{2})\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Universal period charge line (all date formats, optional "to" separator):
#   LABEL  FROM_DATE  [to]  TO_DATE  $AMOUNT
# Handles both DD-MM-YYYY (hyphen) and DD/MM/YYYY (slash), 2- or 4-digit years.
_PERIOD_LINE_RE = re.compile(
    rf"^(?P<label>[A-Za-z][A-Za-z &/\-]{{1,60}}?)\s+"
    rf"(?P<from>{_DATE_ANY})"
    rf"(?:\s+(?:to|[-–])\s+|\s+)"
    rf"(?P<to>{_DATE_ANY})\s+"
    rf"\$?(?P<amount>[\d,]+\.\d{{2}})",
    re.MULTILINE | re.IGNORECASE,
)

# Daily rate line (Central Highlands $/day, Goulburn Valley ¢/day):
#   "Label: From DATE To DATE = N days @ RATE[¢] per day = $TOTAL"
_DAILY_RATE_RE = re.compile(
    rf"(?P<label>[A-Za-z][A-Za-z\s/]{{1,50}}?):\s+"
    rf"(?:[A-Za-z0-9\s:.\-]{{0,30}}?\s+)?"   # optional qualifier e.g. "EQT: 1"
    rf"[Ff]rom\s+(?P<from>{_DATE_ANY})\s+"
    rf"[Tt]o\s+(?P<to>{_DATE_ANY})"
    rf"\s*=\s*(?P<days>\d+)\s+[Dd]ays?\s+@\s+"
    rf"(?P<rate>[\d.]+)(?P<unit>[¢c])?\s+[Pp]er\s+[Dd]ay"
    rf"\s*=\s*\$(?P<total>[\d,]+\.\d{{2}})",
    re.IGNORECASE,
)

# Usage: "23kL @ 131.02¢/kL" or "10.5 kL @ $2.34 per kL"
_USAGE_RE = re.compile(
    r"(?P<volume>[\d]+(?:\.\d+)?)\s*kL?\s*@\s*"
    r"\$?(?P<rate>[\d.]+)(?P<unit>[¢c])?\s*(?:/\s*kL?|per\s+kL?)",
    re.IGNORECASE,
)

# "Total amount due / current charges / balance due …"
_TOTAL_DUE_RE = re.compile(
    r"(?:"
    r"total\s+current\s+(?:charges?|amount)"
    r"|current\s+(?:charges?|amount)\s+(?:due|total)"
    r"|amount\s+(?:now\s+)?due(?:\s+this\s+(?:period|quarter|statement))?"
    r"|total\s+amount\s+due"
    r"|charges?\s+for\s+(?:this\s+)?(?:period|statement)"
    r"|statement\s+total"
    r"|total\s+due(?:\s+this\s+period)?"
    r"|balance\s+due"
    r")"
    r"[^\n$]{0,40}\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Gippsland Water
_GIPPSLAND_BILLING_PERIODS_RE = re.compile(
    r"Gippsland\s+Water\s+billing\s+periods:",
    re.IGNORECASE,
)
_GIPPSLAND_FIXED_SERVICE_RE = re.compile(
    r"(Water\s+Service\s+Charges|Fire\s+Service\s+Charges|Wastewater\s+Service\s+Charges)"
    r"[ \t]+([\-]?\d[\d,]*\.\d{2})",
    re.IGNORECASE,
)

# Stacked-layout parser: "Adjustable Charges:" block where labels and amounts
# appear on separate lines.  Handles PDF text that splits:
#   Water Service Charges
#   Wastewater Service Charges
#   0.00
#   64.69
#   297.24
_GIPPSLAND_ADJ_BLOCK_RE = re.compile(
    r"(?:Adjustable\s+Charges|Fixed\s+Charges|Service\s+Charges)\s*:?\s*"
    r"(.*?)"
    r"(?:Usage\s+Charges|Drainage\s+Charges|Total\s+Charges|Billing\s+Period|$)",
    re.IGNORECASE | re.DOTALL,
)
_GIPPSLAND_SERVICE_LABEL_RE = re.compile(
    r"^(Water\s+Service\s+Charges|Fire\s+Service\s+Charges|Wastewater\s+Service\s+Charges)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_AMOUNT_ONLY_LINE_RE_GW = re.compile(r"^\s*\$?\s*([\d,]+\.\d{2})\s*$", re.MULTILINE)
_GIPPSLAND_CHARGE_BLOCK_RE = re.compile(
    r"Charges\s+levied\s+for\s+billing\s+period:\s*(.*?)"
    r"Gippsland\s+Water\s+billing\s+periods:",
    re.IGNORECASE | re.DOTALL,
)
_GIPPSLAND_SERVICE_CHARGE_RE = re.compile(
    r"Water\s+Service\s+Charges\s+([\-]?\d[\d,]*\.\d{2})",
    re.IGNORECASE,
)

# Outstanding total (for disclosure only — not used as annual rate)
_TOTAL_OUTSTANDING_RE = re.compile(
    r"Total for This Property\s+\$?([\d,]+\.\d{2})", re.IGNORECASE
)
_TOTAL_ALT_RE = re.compile(
    r"Total (?:charges|amount)\s+\$?([\d,]+\.\d{2})", re.IGNORECASE
)

# Primary: "Date of Issue DD Month YYYY"
_DATE_OF_ISSUE_RE = re.compile(
    r"Date\s+of\s+Issue\s+(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})",
    re.IGNORECASE,
)

# Broad: label + any date format (numeric or alpha)
_DATE_LABEL_RE = re.compile(
    r"(?:"
    r"Statement\s+Date"
    r"|Issue\s+Date"
    r"|Date\s+of\s+(?:Issue|Statement|Certification|Preparation|Certificate)"
    r"|Dated?"
    r"|Date"
    r")"
    r"\s*[:\-]?\s*"
    r"(?P<date>" + _DATE_ANY + r")",
    re.IGNORECASE,
)

_MONTH_MAP: dict[str, str] = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "sept": "09", "oct": "10", "nov": "11", "dec": "12",
}

# Triggers — at least one must appear for the extractor to activate
_WATER_TRIGGERS: tuple[str, ...] = (
    "Water Information Statement",
    "Rate Certificate",
    "Information Statement Certificate",
    "Water Rates",
    "Rates Certificate",
    "Water Act 1989",
    "Section 158",
)

# ---------------------------------------------------------------------------
# WaterCandidate dataclass
# ---------------------------------------------------------------------------


@dataclass
class WaterCandidate:
    """A candidate annual water amount produced by one extraction strategy."""

    value: float                        # annual amount in dollars
    source: str                         # strategy name
    confidence: float                   # 0.0–1.0
    quote: str                          # verbatim text snippet
    period_type: str                    # "annual" | "quarterly" | "daily" | "usage" | …
    calculation: dict[str, Any] = field(default_factory=dict)
    needs_review: bool = False          # True when partial match detected

    @property
    def annual_str(self) -> str:
        return f"${self.value:,.2f}"


# Source priority when confidence is tied (higher = more preferred)
_SOURCE_PRIORITY: dict[str, int] = {
    "explicit_annual":         10,
    "gww_annual_column":        9,
    "gippsland_x3":             9,
    "period_sum_annual":        8,
    "period_sum_quarterly":     8,
    "period_sum_semi_annual":   8,
    "period_sum_tri_annual":    8,
    "period_per_row":           7,
    "total_due_annualised":     5,
    "daily_rate_x365":          4,
    "omni_structured_rows":     4,  # same tier as daily_rate — rescue case
    "usage_estimate":           1,
}


def _candidate_rank(c: WaterCandidate) -> tuple[float, int]:
    return (c.confidence, _SOURCE_PRIORITY.get(c.source, 0))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _doc_text(doc: Document) -> str:
    return doc.raw_text or doc.normalized_text or ""


def _make_source(doc: Document, quote: str) -> Source:
    return Source(file=doc.filename, page=None, quote=quote[:300], quote_verified=True)


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
    return float(str(s).replace("$", "").replace(",", "").strip())


# ---------------------------------------------------------------------------
# Universal date parsing
# ---------------------------------------------------------------------------


def _parse_any_date(s: str) -> date | None:
    """Parse a date string in any supported format.

    Formats:
      DD/MM/YY   DD/MM/YYYY   DD-MM-YY   DD-MM-YYYY
      DD Mon YY  DD-Mon-YYYY  DD Mon YYYY
    2-digit years are promoted to 2000+.
    """
    s = s.strip()
    # Numeric: DD/MM/YY(YY) or DD-MM-YY(YY)
    m = re.match(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            pass
    # Alpha: DD Mon YYYY or DD-Mon-YYYY (e.g. 29-Jan-2026, 22 Sep 25)
    m = re.match(r"(\d{1,2})[\s\-]([A-Za-z]{3,9})[\s\-](\d{2,4})$", s)
    if m:
        mo_str = _MONTH_MAP.get(m.group(2).lower())
        if mo_str:
            d, y = int(m.group(1)), int(m.group(3))
            if y < 100:
                y += 2000
            try:
                return date(y, int(mo_str), d)
            except ValueError:
                pass
    return None


def _days_between(from_str: str, to_str: str) -> int | None:
    """Return calendar days between two date strings (any format)."""
    s = _parse_any_date(from_str)
    e = _parse_any_date(to_str)
    if s is None or e is None:
        return None
    delta = (e - s).days
    return delta if delta > 0 else None


# ---------------------------------------------------------------------------
# Period classification
# ---------------------------------------------------------------------------


def _classify_period(days: int | None) -> tuple[str, int]:
    """Return (period_label, annualisation_multiplier) from day count."""
    if days is None:
        return "quarterly", 4   # Victorian default
    if days <= 100:
        return "quarterly", 4
    if days <= 140:
        return "tri_annual", 3  # ~4 months (Gippsland Water)
    if days <= 200:
        return "semi_annual", 2
    if days <= 380:
        return "annual", 1
    return "multi_year", 1      # can't reliably annualise


# ---------------------------------------------------------------------------
# Candidate parsers
# ---------------------------------------------------------------------------


def _parse_explicit_annual(text: str) -> list[WaterCandidate]:
    """Strategy 1 — document explicitly states annual total."""
    # "Total annual charges $737.01"
    m = _TOTAL_ANNUAL_RE.search(text)
    if m:
        try:
            val = _parse_dollar(m.group(1))
            if val > 0:
                return [WaterCandidate(
                    value=val, source="explicit_annual", confidence=0.99,
                    quote=m.group(0).strip(), period_type="annual",
                    calculation={"label": "Total annual charges", "amount": val},
                )]
        except ValueError:
            pass

    # Broader annual-label fallback
    m = _ANNUAL_LABEL_RE.search(text)
    if m:
        try:
            val = _parse_dollar(m.group(1))
            if val > 0:
                return [WaterCandidate(
                    value=val, source="explicit_annual", confidence=0.95,
                    quote=m.group(0).strip(), period_type="annual",
                    calculation={"label": "annual label", "amount": val},
                )]
        except ValueError:
            pass

    return []


def _parse_gww_annual_column(text: str) -> list[WaterCandidate]:
    """Strategy 2 — GWW per-line annual column: sum the annual_charge column."""
    matches = list(_GWW_ANNUAL_LINE_RE.finditer(text))
    if not matches:
        return []
    try:
        total = sum(_parse_dollar(m.group("annual")) for m in matches)
        if total <= 0:
            return []
        quote = f"Sum of {len(matches)} GWW annual-column amounts = ${total:,.2f}"
        return [WaterCandidate(
            value=total, source="gww_annual_column", confidence=0.95,
            quote=quote, period_type="annual",
            calculation={"rows": len(matches), "annual_total": total},
        )]
    except ValueError:
        return []


def _parse_gippsland_stacked_block(text: str) -> list[tuple[str, float]]:
    """Parse stacked Gippsland service-charge layouts where labels and amounts
    are on separate lines within an Adjustable/Fixed/Service Charges block.

    Layout handled:
        Water Service Charges          ← label lines first
        Wastewater Service Charges
        0.00                           ← amounts follow (including zeros)
        64.69
        297.24

    We collect all service-label lines into a buffer, then all amount lines
    that follow (until the next non-amount non-empty line), and zip them in
    order.  Zero amounts are kept so pairing stays aligned.
    """
    included: list[tuple[str, float]] = []
    block_m = _GIPPSLAND_ADJ_BLOCK_RE.search(text)
    if not block_m:
        return included

    block = block_m.group(1)
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]

    i = 0
    while i < len(lines):
        # Accumulate a run of service-label lines
        label_run: list[str] = []
        while i < len(lines) and _GIPPSLAND_SERVICE_LABEL_RE.match(lines[i]):
            label_run.append(lines[i])
            i += 1

        if not label_run:
            i += 1
            continue

        # Accumulate the amount lines that immediately follow
        amount_run: list[float] = []
        while i < len(lines):
            am = _AMOUNT_ONLY_LINE_RE_GW.match(lines[i])
            if not am:
                break
            try:
                amount_run.append(float(am.group(1).replace(",", "")))
            except ValueError:
                pass
            i += 1

        # Pair labels with amounts positionally.
        # Some PDFs emit a leading 0.00 for a charge type not present at this
        # property (e.g. Fire Service Charges = $0.00).  In that case there are
        # more amounts than service labels.  Take the last len(label_run) amounts
        # so each label aligns with its own value.
        aligned_amounts = amount_run[-len(label_run):] if len(amount_run) >= len(label_run) else amount_run
        for lbl, amt in zip(label_run, aligned_amounts):
            if amt > 0:
                included.append((lbl, amt))

    return included


def _parse_gippsland_annual(text: str) -> list[WaterCandidate]:
    """Strategy 3 — Gippsland Water tri-annual fixed service charges × 3."""
    if "Gippsland Water" not in text or not _GIPPSLAND_BILLING_PERIODS_RE.search(text):
        return []

    # Strategy 3a: stacked block (labels and amounts on separate lines)
    stacked = _parse_gippsland_stacked_block(text)
    if stacked:
        period_total = sum(v for _, v in stacked)
        annual = round(period_total * 3, 2)
        parts = ", ".join(f"{lbl} ${v:,.2f}" for lbl, v in stacked)
        quote = f"Gippsland fixed service (stacked): {parts} -> ${period_total:,.2f} × 3 = ${annual:,.2f}"
        return [WaterCandidate(
            value=annual, source="gippsland_x3", confidence=0.96,
            quote=quote, period_type="tri_annual",
            calculation={"rows": stacked, "period_total": period_total, "multiplier": 3, "annual": annual},
        )]

    # Strategy 3b: inline fixed-service lines
    fixed_matches = list(_GIPPSLAND_FIXED_SERVICE_RE.finditer(text))
    if fixed_matches:
        included: list[tuple[str, float]] = []
        for m in fixed_matches:
            label = _compact(m.group(1))
            try:
                v = float(m.group(2).replace(",", ""))
            except ValueError:
                continue
            if v > 0:
                included.append((label, v))
        if included:
            # Partial-match check: if Water and Wastewater labels both appear in
            # text but only one was captured inline, the stacked layout probably
            # hid one value — mark needs_review so the Omni gate fires.
            has_water_label = bool(re.search(r"Water\s+Service\s+Charges", text, re.IGNORECASE))
            has_waste_label = bool(re.search(r"Wastewater\s+Service\s+Charges", text, re.IGNORECASE))
            matched_labels = {lbl.lower() for lbl, _ in included}
            partial = (
                (has_water_label and "water service charges" not in matched_labels)
                or (has_waste_label and "wastewater service charges" not in matched_labels)
            )
            period_total = sum(v for _, v in included)
            annual = round(period_total * 3, 2)
            parts = ", ".join(f"{lbl} ${v:,.2f}" for lbl, v in included)
            quote = (
                f"Gippsland fixed service: {parts} -> "
                f"${period_total:,.2f} × 3 = ${annual:,.2f}"
            )
            if partial:
                quote += " [WARNING: partial match — some service charges may be in stacked layout]"
            return [WaterCandidate(
                value=annual, source="gippsland_x3", confidence=0.96,
                quote=quote, period_type="tri_annual",
                needs_review=partial,
                calculation={
                    "rows": included, "period_total": period_total,
                    "multiplier": 3, "annual": annual,
                },
            )]

    # Fallback: charge block
    block_m = _GIPPSLAND_CHARGE_BLOCK_RE.search(text)
    if block_m:
        raw_amounts = re.findall(r"(?<!\d)(-?\d[\d,]*\.\d{2})(?!\d)", block_m.group(1))
        positives = [float(r.replace(",", "")) for r in raw_amounts
                     if float(r.replace(",", "")) > 0]
        if positives:
            v = positives[0]
            annual = round(v * 3, 2)
            quote = (
                f"Gippsland charge-block first positive "
                f"${v:,.2f} × 3 = ${annual:,.2f}"
            )
            return [WaterCandidate(
                value=annual, source="gippsland_x3", confidence=0.92,
                quote=quote, period_type="tri_annual",
                calculation={"period_total": v, "multiplier": 3, "annual": annual},
            )]

    # Last resort: service charge line
    svc_m = _GIPPSLAND_SERVICE_CHARGE_RE.search(text)
    if svc_m:
        try:
            v = float(svc_m.group(1).replace(",", ""))
            if v > 0:
                annual = round(v * 3, 2)
                quote = (
                    f"Gippsland Water Service Charges "
                    f"${v:,.2f} × 3 = ${annual:,.2f}"
                )
                return [WaterCandidate(
                    value=annual, source="gippsland_x3", confidence=0.90,
                    quote=quote, period_type="tri_annual",
                    calculation={"period_total": v, "multiplier": 3, "annual": annual},
                )]
        except ValueError:
            pass

    return []


def _parse_period_charge_lines(text: str) -> list[WaterCandidate]:
    """Strategy 4 — charge lines with date ranges (all date formats).

    Each row: LABEL  FROM_DATE  [to]  TO_DATE  $AMOUNT
    Per-row period detection — every row gets its own annualisation multiplier.
    Handles Yarra Valley (quarterly), South East Water (quarterly slash dates),
    Westernport Water (mixed periods), and anything else with DATE … DATE $AMOUNT rows.
    """
    matches = list(_PERIOD_LINE_RE.finditer(text))
    if not matches:
        return []

    # Skip obvious header/junk rows
    _LABEL_SKIP = {"from", "to", "date", "period", "description", "charge", "label"}

    row_data: list[dict] = []
    for m in matches:
        label = _compact(m.group("label"))
        if label.lower() in _LABEL_SKIP:
            continue
        try:
            amount = _parse_dollar(m.group("amount"))
        except ValueError:
            continue
        if amount <= 0:
            continue
        days = _days_between(m.group("from"), m.group("to"))
        period_type, multiplier = _classify_period(days)
        annual = amount * multiplier
        row_data.append({
            "label": label,
            "amount": amount,
            "days": days,
            "period_type": period_type,
            "multiplier": multiplier,
            "annual": annual,
            "from": m.group("from"),
            "to": m.group("to"),
        })

    if not row_data:
        return []

    multipliers_used = {r["multiplier"] for r in row_data}
    total_annual = sum(r["annual"] for r in row_data)
    mixed = len(multipliers_used) > 1
    dominant_type = (
        row_data[0]["period_type"] if not mixed
        else "mixed"
    )
    source = (
        f"period_sum_{dominant_type}" if not mixed
        else "period_per_row"
    )

    # Confidence: explicit annual -> 0.97, quarterly -> 0.95, mixed -> 0.82
    conf_map = {
        "annual": 0.97, "quarterly": 0.95, "semi_annual": 0.92,
        "tri_annual": 0.92, "multi_year": 0.80,
    }
    confidence = 0.82 if mixed else conf_map.get(dominant_type, 0.88)

    row_desc = "; ".join(
        f"{r['label']} ${r['amount']:,.2f}(×{r['multiplier']})" for r in row_data
    )
    quote = f"Period charge lines: {row_desc} -> ${total_annual:,.2f}"

    return [WaterCandidate(
        value=total_annual,
        source=source,
        confidence=confidence,
        quote=quote,
        period_type=dominant_type,
        calculation={
            "rows": len(row_data),
            "row_detail": row_data,
            "total_annual": total_annual,
            "mixed_periods": mixed,
        },
    )]


def _parse_daily_rate_lines(text: str) -> list[WaterCandidate]:
    """Strategy 5 — daily rate lines (Central Highlands $/day, Goulburn Valley ¢/day).

    Format: "Label: From DATE To DATE = N days @ RATE[¢] per day = $TOTAL"
    Handles 2-digit and 4-digit years. ¢ unit -> divide by 100.
    Annual = sum(daily_dollars) × 365/366 (leap-year aware).
    """
    matches = list(_DAILY_RATE_RE.finditer(text))
    if not matches:
        return []

    daily_rows: list[dict] = []
    for m in matches:
        try:
            rate_raw = float(m.group("rate"))
            unit = (m.group("unit") or "").lower().strip()
            daily_dollars = rate_raw / 100.0 if unit in ("¢", "c") else rate_raw
            daily_rows.append({
                "label": m.group("label").strip(),
                "rate_raw": rate_raw,
                "unit": unit or "$",
                "daily_dollars": daily_dollars,
                "days": int(m.group("days")),
                "period_total": _parse_dollar(m.group("total")),
                "from": m.group("from"),
                "to": m.group("to"),
            })
        except (ValueError, AttributeError):
            continue

    if not daily_rows:
        return []

    # Sanity: if both Sewerage and Water service fees appear in the text but only
    # one matched (e.g. because one had an EQT qualifier), flag for review.
    has_sewerage = bool(re.search(r"Sewerage\s+Service\s+Fee", text, re.IGNORECASE))
    has_water = bool(re.search(r"Water\s+Service\s+Fee", text, re.IGNORECASE))
    matched_labels = " ".join(r["label"] for r in daily_rows).lower()
    partial_match = (
        (has_sewerage and "sewerage" not in matched_labels)
        or (has_water and "water" not in matched_labels)
    )

    total_daily_dollars = sum(r["daily_dollars"] for r in daily_rows)

    # Leap-year check using first "From" date
    from_date = _parse_any_date(daily_rows[0]["from"])
    if from_date:
        doc_year = from_date.year
        days_in_year = 366 if calendar.isleap(doc_year) else 365
    else:
        doc_year = None
        days_in_year = 365

    annual = total_daily_dollars * days_in_year
    leap_note = f" (leap {doc_year})" if days_in_year == 366 else ""
    labels_str = ", ".join(
        f"{r['label']} @{r['rate_raw']:.4f}"
        f"{'¢' if r['unit'] in ('¢', 'c') else '$'}/day"
        for r in daily_rows
    )
    quote = (
        f"Daily rates: {labels_str} -> ${total_daily_dollars:.6f}/day "
        f"× {days_in_year}{leap_note} = ${annual:,.2f}"
    )

    if partial_match:
        quote += " [WARNING: Sewerage or Water service fee present in text but not matched — result may be incomplete]"

    return [WaterCandidate(
        value=annual,
        source="daily_rate_x365",
        confidence=0.80,
        quote=quote,
        period_type="daily",
        needs_review=partial_match,
        calculation={
            "rows": daily_rows,
            "total_daily_dollars": total_daily_dollars,
            "days_in_year": days_in_year,
            "doc_year": doc_year,
            "annual": annual,
        },
    )]


def _parse_usage_candidates(text: str) -> list[WaterCandidate]:
    """Strategy 6 — usage volume × rate (rough annual estimate, lowest priority).

    Usage is highly variable between billing periods — treat these as a
    last-resort signal only. Confidence is low (0.55).
    """
    matches = list(_USAGE_RE.finditer(text))
    if not matches:
        return []

    candidates: list[WaterCandidate] = []
    for m in matches:
        try:
            volume = float(m.group("volume"))
            rate_raw = float(m.group("rate"))
            unit = (m.group("unit") or "").lower().strip()
            rate_dol_per_kl = rate_raw / 100.0 if unit in ("¢", "c") else rate_raw
            usage_cost = volume * rate_dol_per_kl
            annual = usage_cost * 4   # assume quarterly
            quote = (
                f"{volume}kL @ {rate_raw}"
                f"{'¢' if unit in ('¢', 'c') else '$'}/kL = "
                f"${usage_cost:,.2f} × 4 (est.) = ${annual:,.2f}"
            )
            candidates.append(WaterCandidate(
                value=annual,
                source="usage_estimate",
                confidence=0.55,
                quote=quote,
                period_type="usage",
                calculation={
                    "volume_kl": volume,
                    "rate_dollars_per_kl": rate_dol_per_kl,
                    "usage_cost": usage_cost,
                },
            ))
        except (ValueError, AttributeError):
            continue

    return candidates


def _parse_total_due(text: str) -> list[WaterCandidate]:
    """Strategy 7 — 'Total amount due / current charges' fallback.

    Lower confidence — may include outstanding debt from previous periods.
    Annualised using any date range found in the document.
    """
    m = _TOTAL_DUE_RE.search(text)
    if not m:
        return []
    try:
        val = _parse_dollar(m.group(1))
        if val <= 0:
            return []
    except ValueError:
        return []

    period_m = re.search(
        rf"({_DATE_ANY})\s+(?:to|[-–])\s+({_DATE_ANY})", text, re.IGNORECASE
    )
    days = None
    if period_m:
        days = _days_between(period_m.group(1), period_m.group(2))
    period_type, multiplier = _classify_period(days)
    annual = round(val * multiplier, 2)
    quote = (
        f"Total due '{m.group(0).strip()}' -> "
        f"${val:,.2f} × {multiplier} ({period_type}) = ${annual:,.2f}"
    )
    return [WaterCandidate(
        value=annual,
        source="total_due_annualised",
        confidence=0.75,
        quote=quote,
        period_type=period_type,
        calculation={"total_due": val, "multiplier": multiplier, "annual": annual},
    )]


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

# Sources excluded from automatic winner selection (usage is too variable).
# They remain in the candidate list for debug output.
_LOW_PRIORITY_SOURCES: frozenset[str] = frozenset({"usage_estimate"})


def _choose_best_candidate(
    candidates: list[WaterCandidate],
) -> WaterCandidate | None:
    """Return the highest-ranked candidate.

    Usage-estimate candidates are excluded unless they are the only option.
    """
    eligible = [c for c in candidates if c.source not in _LOW_PRIORITY_SOURCES]
    if not eligible:
        eligible = candidates  # last resort — usage only
    if not eligible:
        return None
    return max(eligible, key=_candidate_rank)


# ---------------------------------------------------------------------------
# Candidate -> Fact objects
# ---------------------------------------------------------------------------


def _candidate_to_facts(doc: Document, winner: WaterCandidate) -> list[Fact]:
    """Convert the winning WaterCandidate into standard Fact objects."""
    annual_str = winner.annual_str
    period_type = winner.period_type
    quote = winner.quote[:300]
    calc = winner.calculation

    # Human-readable period note
    if period_type == "quarterly":
        period_note = "quarterly × 4"
    elif period_type == "tri_annual":
        period_note = "tri-annual × 3"
    elif period_type == "semi_annual":
        period_note = "semi-annual × 2"
    elif period_type == "daily":
        days = calc.get("days_in_year", 365)
        period_note = f"daily × {days} days"
    elif period_type == "annual":
        period_note = "explicit annual"
    elif period_type == "mixed":
        period_note = "per-row mixed periods"
    else:
        period_note = period_type

    notes_base = (
        f"Annual water rate ({period_note}, source={winner.source}): "
        f"{winner.quote[:150]}"
    )

    facts: list[Fact] = [
        _make_fact(
            doc, P.RATES_WATER_ANNUAL, annual_str, quote,
            confidence=winner.confidence,
            notes=notes_base,
        ),
        _make_fact(
            doc, P.RATES_WATER_CERT_AMOUNT_ANNUAL, annual_str, quote,
            confidence=winner.confidence,
            notes=notes_base,
        ),
        _make_fact(
            doc, P.RATES_WATER_CERT_PERIOD_TYPE, period_type, quote,
            confidence=winner.confidence,
            notes=f"Period type: {period_note}",
        ),
    ]

    # Calculated / uncertain amounts -> needs_review
    _needs_review_sources = {
        "daily_rate_x365", "usage_estimate", "total_due_annualised", "period_per_row",
    }
    if winner.source in _needs_review_sources or winner.needs_review:
        facts.append(_make_fact(
            doc, "rates.water.needs_review", True, quote,
            confidence=0.99,
            notes=(
                f"Amount calculated from {winner.source} — verify against "
                "authority's published annual tariff or add a DB copy rule."
            ),
        ))

    # Daily-rate billing -> prompt for copy-rule DB entry
    if winner.source == "daily_rate_x365":
        facts.append(_make_fact(
            doc, "rates.water.copy_rules_recommended", True, quote,
            confidence=0.95,
            notes="Daily-rate billing detected. DB copy rule lookup recommended.",
        ))

    return facts


# ---------------------------------------------------------------------------
# Non-amount fact extractors
# ---------------------------------------------------------------------------


# Matches unit addresses like "1/14 BELL ST", "UNIT 3/14 BELL ST",
# "Property Address: 1/14 BELL STREET", or bare "Unit 3/14" anywhere in text.
_UNIT_ADDR_RE = re.compile(
    r"(?:"
    r"(?:Property\s+Address[:\s]+|Address[:\s]+)"   # labelled
    r"|(?:^|\s)"                                      # or start/whitespace
    r")"
    r"(?:UNIT\s+)?(\d+)\s*/\s*\d+",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_unit_number(doc: Document, text: str) -> list[Fact]:
    """Return a unit_number fact when the property is a strata/unit address."""
    for m in _UNIT_ADDR_RE.finditer(text):
        unit_no = m.group(1).strip()
        if unit_no and int(unit_no) > 0:
            return [_make_fact(
                doc, P.RATES_WATER_UNIT_NUMBER, unit_no, m.group(0).strip(),
                confidence=0.95,
                notes=f"Unit {unit_no} detected from address in water authority document",
            )]
    return []


def _extract_authority(doc: Document, text: str) -> list[Fact]:
    """Return authority name fact, normalising raw document wording to canonical name."""
    text_lower = text.lower()
    # Check aliases first (document uses a different legal name)
    for raw, canonical in _AUTHORITY_ALIASES.items():
        if raw in text_lower:
            return [_make_fact(
                doc, P.RATES_WATER_AUTHORITY, canonical, raw, confidence=0.99,
                notes=f"Alias match: '{raw}' -> '{canonical}'",
            )]
    # Canonical names
    for name in _KNOWN_AUTHORITIES:
        if name.lower() in text_lower:
            return [_make_fact(doc, P.RATES_WATER_AUTHORITY, name, name, confidence=0.99)]
    return []


def _extract_certificate_number(doc: Document, text: str) -> list[Fact]:
    m = _CERT_NO_RE.search(text)
    if not m:
        return []
    return [_make_fact(doc, P.RATES_WATER_CERTIFICATE_NUMBER, m.group(1), m.group(0))]


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
        _make_fact(doc, P.PROPERTY_ADDRESS, address, quote, confidence=0.92,
                   notes="from water authority rates table"),
        _make_fact(doc, P.PROPERTY_LOT_NUMBER, lot, quote, confidence=0.95),
        _make_fact(doc, P.PROPERTY_PLAN_NUMBER, plan, quote, confidence=0.95),
        _make_fact(doc, P.RATES_WATER_PROPERTY_NUMBER, propnum, quote),
        _make_fact(doc, P.RATES_WATER_ACCOUNT_NUMBER, propnum, quote),
        _make_fact(doc, P.RATES_WATER_PROPERTY_TYPE, ptype, quote),
    ]


def _extract_outstanding(doc: Document, text: str) -> list[Fact]:
    """Extract total outstanding for disclosure — not used as annual rate."""
    m = _TOTAL_OUTSTANDING_RE.search(text) or _TOTAL_ALT_RE.search(text)
    if not m:
        return []
    return [_make_fact(
        doc, P.RATES_WATER_TOTAL_OUTSTANDING, f"${m.group(1)}", m.group(0),
        notes=(
            "'Total for This Property' / 'Total amount' includes current charges plus any "
            "overdue amounts — stored for disclosure only, not used as annual rate."
        ),
    )]


def _normalise_date_str(raw: str) -> str | None:
    """Normalise any supported date format to DD/MM/YYYY.

    Returns None if the string cannot be parsed.
    """
    d = _parse_any_date(raw.strip())
    if d is None:
        return None
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _extract_doc_date(doc: Document, text: str) -> list[Fact]:
    """Extract the certificate issue date from a water authority statement.

    Tries multiple label patterns in priority order:
      1. "Date of Issue DD Month YYYY"  (classic format)
      2. Any labelled date: "Statement Date:", "Issue Date:", "Dated:", etc.
    Normalises all formats to DD/MM/YYYY.
    """
    # Strategy 1: classic "Date of Issue DD Month YYYY"
    m = _DATE_OF_ISSUE_RE.search(text)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        date_str = f"{int(day):02d}/{_month_to_num(month)}/{year}"
        return [_make_fact(
            doc, P.DOCS_WATER_CERT_DATE, date_str, _compact(m.group(0)),
            confidence=0.97, notes="water authority statement issue date",
        )]

    # Strategy 2: broader labelled-date patterns (numeric or alpha date formats)
    for m2 in _DATE_LABEL_RE.finditer(text):
        raw_date = m2.group("date").strip()
        date_str = _normalise_date_str(raw_date)
        if date_str:
            return [_make_fact(
                doc, P.DOCS_WATER_CERT_DATE, date_str, _compact(m2.group(0)),
                confidence=0.90, notes="water authority statement date (label match)",
            )]

    return []


_WATER_SERVICE_TRIGGERS: tuple[str, ...] = (
    "Water Service Charge",   # Yarra Valley, SEW, Barwon …
    "Water Service Fee",      # Goulburn Valley Water
    "Water Rates",
)

_SEWER_SERVICE_TRIGGERS: tuple[str, ...] = (
    "Sewer Service Charge",
    "Sewerage Service Charge",
    "Sewerage Service Fee",   # Goulburn Valley Water
    "Wastewater Service",
    "Sewerage",               # broad fallback
)


def _extract_services(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    water_hit = next((t for t in _WATER_SERVICE_TRIGGERS if t in text), None)
    if water_hit:
        facts.append(_make_fact(
            doc, P.service_connected("water"), True,
            water_hit, confidence=0.95,
            notes="implied by water authority charging a water service fee",
        ))
    sewer_hit = next((t for t in _SEWER_SERVICE_TRIGGERS if t in text), None)
    if sewer_hit:
        facts.append(_make_fact(
            doc, P.service_connected("sewerage"), True,
            sewer_hit, confidence=0.95,
            notes="implied by water authority charging a sewerage/wastewater fee",
        ))
    return facts


# ---------------------------------------------------------------------------
# Debug logger
# ---------------------------------------------------------------------------


def _water_debug_log(
    doc: Document,
    authority: str | None,
    winner: WaterCandidate | None,
    all_candidates: list[WaterCandidate],
) -> None:
    fname = doc.filename or "unknown"
    print(f"\n  +- WATER CANDIDATE ENGINE [{fname}]")
    print(f"  |  Authority          : {authority or '(none)'}")

    if winner:
        print(f"  |  WINNER source      : {winner.source}")
        print(f"  |  WINNER amount      : {winner.annual_str}  (conf={winner.confidence:.2f})")
        print(f"  |  WINNER period type : {winner.period_type}")
    else:
        print(f"  |  WINNER             : (none — no candidates produced an amount)")

    if all_candidates:
        print(f"  |  All candidates ({len(all_candidates)}):")
        for c in sorted(all_candidates, key=_candidate_rank, reverse=True):
            mark = "[W]" if (winner and c is winner) else "   "
            print(
                f"  |    {mark} {c.source:<32} {c.annual_str:<12} "
                f"conf={c.confidence:.2f}  type={c.period_type}"
            )
            print(f"  |        {c.quote[:100]}")
    else:
        print(f"  |  All candidates: (none)")

    if winner and winner.source == "daily_rate_x365":
        print(
            f"  |  ! DB copy rule recommended — "
            f"add entry for '{authority or 'this authority'}'"
        )

    print(f"  +---------------------------------------------")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_water_authority_certificate_facts(
    doc: Document,
    *,
    ai_client=None,
) -> list[Fact]:
    """Extract facts from a Victorian water authority information statement.

    ai_client — optional AIClient instance.  When provided the Nemotron Omni
    perception sub-agent is invoked as a fallback when rule-based candidates
    are weak or conflicting.
    """
    text = _doc_text(doc)
    if not text:
        return []
    if not any(t in text for t in _WATER_TRIGGERS):
        return []

    facts: list[Fact] = []

    # --- Authority name ---
    auth_facts = _extract_authority(doc, text)
    facts.extend(auth_facts)
    authority_name: str | None = str(auth_facts[0].value) if auth_facts else None

    # --- Non-amount facts ---
    facts.extend(_extract_unit_number(doc, text))
    facts.extend(_extract_certificate_number(doc, text))
    facts.extend(_extract_property_row(doc, text))
    facts.extend(_extract_outstanding(doc, text))
    facts.extend(_extract_doc_date(doc, text))
    facts.extend(_extract_services(doc, text))

    # --- Candidate engine ---
    all_candidates: list[WaterCandidate] = []
    all_candidates += _parse_explicit_annual(text)
    all_candidates += _parse_gww_annual_column(text)
    all_candidates += _parse_gippsland_annual(text)
    all_candidates += _parse_period_charge_lines(text)
    all_candidates += _parse_daily_rate_lines(text)
    all_candidates += _parse_usage_candidates(text)

    # total_due is a noisy fallback — only include it when no better candidate
    # (period lines or daily rates) has produced an amount, to prevent
    # accidental annualisation of statement balances.
    _BETTER_SOURCES = {
        "explicit_annual", "gww_annual_column", "gippsland_x3",
        "period_per_row",
        *[f"period_sum_{t}" for t in ("annual", "quarterly", "semi_annual", "tri_annual", "mixed")],
        "daily_rate_x365",
    }
    if not any(c.source in _BETTER_SOURCES for c in all_candidates):
        all_candidates += _parse_total_due(text)

    # --- Omni perception sub-agent (fallback) ---
    if ai_client is not None:
        _initial = _choose_best_candidate(all_candidates)
        _conflict = (
            len([c for c in all_candidates if c.value > 0]) >= 2
            and _initial is not None
            and any(
                abs(c.value - _initial.value) > 1.0
                for c in all_candidates
                if c is not _initial and c.value > 0
            )
        )
        _suspicious = (
            not _initial
            or _initial.needs_review  # partial match on any source
        )
        if should_call_omni(
            best_source=_initial.source if _initial else None,
            best_value=_initial.value if _initial else None,
            conflict=_conflict,
            suspicious=_suspicious,
            from_balance=False,
            lower_than_balance=False,
            unknown_table_shape=False,
            candidate_count=len(all_candidates),
        ):
            omni_reason = (
                f"conflict={_conflict} suspicious={_suspicious} "
                f"best={_initial.source if _initial else 'none'}"
                f"={_initial.annual_str if _initial else '?'}"
            )
            omni_rows = classify_table_with_omni(
                text=text,
                ai_client=ai_client,
                doc_type="water",
            )
            omni_result = annual_from_omni_rows(omni_rows)
            _model_name = getattr(ai_client, "model", "unknown")
            if omni_result:
                omni_val, omni_quote = omni_result
                omni_ok, _ = validate_omni_candidate(omni_val, omni_rows, set())
                if omni_ok:
                    all_candidates.append(
                        WaterCandidate(
                            value=omni_val,
                            source="omni_structured_rows",
                            confidence=0.82,
                            quote=omni_quote,
                            period_type="annual",
                        )
                    )
                omni_write_debug_log(
                    filename=doc.filename or "",
                    doc_type="water",
                    called=True,
                    reason=omni_reason,
                    model=_model_name,
                    rows=omni_rows,
                    candidate=omni_val if omni_result else None,
                    accepted=omni_ok if omni_result else False,
                )

    winner = _choose_best_candidate(all_candidates)
    if winner:
        facts.extend(_candidate_to_facts(doc, winner))

    _water_debug_log(doc, authority_name, winner, all_candidates)

    return facts
