"""Water authority certificate extractor v2 — layout-aware row-classifier architecture.

Production pipeline (8 steps)
-------------------------------
  1. OCR / text extraction
  2. Identify rates/certificate page (first 3000 chars used for layout detection)
  3. Deterministic layout detector  → WaterLayoutType
  4. AI layout classifier           → only when deterministic returns "unknown"
  5. Layout-specific parser         → list[RawRow]
  6. Row classifier safety layer    → classifyRow(row) → RowType
  7. Deterministic annual calculation → chooseWinner() / annualiseRow()
  8. confidence + needs_review output

Layout types
------------
  period_row_table       rows contain label + FROM date + TO date + amount
  annual_column_table    table has an explicit "Annual Charge" column (GWW)
  daily_rate_lines       rows use "N Days @ X Per Day" (Central Highlands)
  stacked_label_amounts  labels and amounts appear on separate lines/columns
  statement_balance_only only a total/balance amount is visible
  unknown

Row types (step 6)
------------------
    recurring_charge          fixed service / access charge (annualise by period)
    daily_access_charge       $/day line (annualise × days-in-year)
    usage_charge              volume-based usage (exclude from annual)
    payment_or_credit         payment / credit note (EXCLUDE — never annual)
    arrears_or_brought_forward overdue amounts (EXCLUDE)
    balance_or_total_due      "Total amount due" etc. (use only as last fallback)
    metadata                  dates, property info, certificate numbers (ignore)
    unknown                   AI classifies if ambiguous

Pipeline per document:
  1. detect_layout(text) → WaterLayoutType  (deterministic, AI fallback)
  2. layout-routed _extract_raw_rows()
  3. classifyRow(row) → RowType   (structure-first, regex backup, AI for unknowns)
  4. annualiseRow(row)  for recurring / daily rows
  5. chooseWinner(rows) → annual_amount      (sum of annualised recurring rows)
  6. canUseTotalDueFallback() guard          (only if zero recurring rows found)

Global EXCLUDE patterns mean Payments / Arrears / Balances can NEVER become
the annual amount — fixes the Westernport Water $215.02 Payments problem.

Expected output shape
---------------------
{
    "authority":      str,
    "annual_amount":  float,
    "confidence":     float,
    "needs_review":   bool,
    "strategy":       str,
    "breakdown":      [{"label": str, "amount": float, "multiplier": int}],
    "excluded_rows":  [{"label": str, "amount": float, "reason": str}],
    "warnings":       [str],
}
"""
from __future__ import annotations

import calendar
import dataclasses
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:water_authority_certificate_v2"

# ---------------------------------------------------------------------------
# Layout classification
# ---------------------------------------------------------------------------

class WaterLayoutType(str, Enum):
    period_row_table       = "period_row_table"       # label + FROM date + TO date + $amount
    annual_column_table    = "annual_column_table"    # explicit Annual Charge column (GWW)
    daily_rate_lines       = "daily_rate_lines"       # N Days @ X Per Day
    stacked_label_amounts  = "stacked_label_amounts"  # labels/amounts on separate lines
    statement_balance_only = "statement_balance_only" # only total/balance visible
    unknown                = "unknown"


# Deterministic layout signals — checked in priority order
_LAYOUT_ANNUAL_COLUMN_RE = re.compile(
    r"Total\s+annual\s+charges?|Annual\s+[Cc]harge|annual\s+amount",
    re.IGNORECASE,
)
_LAYOUT_DAILY_RATE_RE = re.compile(
    r"\d+\s+Days?\s+@\s+[\d.]+\s+(?:[Pp]er\s+[Dd]ay|/[Dd]ay)",
    re.IGNORECASE,
)
_LAYOUT_PERIOD_ROW_RE = re.compile(
    r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\s+(?:to|[-–])\s+\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}",
    re.IGNORECASE,
)
_LAYOUT_STACKED_RE = re.compile(
    r"^(?:Water|Sewerage|Wastewater|Service|Fire)\s+(?:Service\s+)?Charges?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def detect_layout_deterministic(text: str) -> WaterLayoutType:
    """Fast regex-based layout detection. Returns unknown when ambiguous."""
    # Check in priority order — most specific first
    if _LAYOUT_ANNUAL_COLUMN_RE.search(text):
        return WaterLayoutType.annual_column_table
    if _LAYOUT_DAILY_RATE_RE.search(text):
        return WaterLayoutType.daily_rate_lines
    if _LAYOUT_PERIOD_ROW_RE.search(text):
        return WaterLayoutType.period_row_table
    # Stacked: isolated service-charge label lines with amounts on adjacent lines
    if len(_LAYOUT_STACKED_RE.findall(text)) >= 2:
        return WaterLayoutType.stacked_label_amounts
    return WaterLayoutType.unknown


_LAYOUT_CLASSIFIER_PROMPT = """\
Classify this water authority rates page layout.

Return JSON only — no markdown, no explanation.

Allowed layout_type values:
- period_row_table: rows contain label + from date + to date + amount
- annual_column_table: table has an explicit Annual Charge column
- daily_rate_lines: rows use "N Days @ X Per Day" or "N days @ X per day"
- stacked_label_amounts: labels and amounts are on separate lines or columns
- statement_balance_only: only a total/balance amount is visible, no charge breakdown
- unknown

Return:
{
  "layout_type": "...",
  "confidence": 0.0,
  "reason": "one sentence",
  "recommended_extractor": "one of the layout_type values above"
}

Page text:
"""


def detect_layout_ai(text: str, ai_client: Any) -> WaterLayoutType:
    """AI layout classifier — called only when deterministic check returns unknown."""
    if ai_client is None:
        return WaterLayoutType.unknown
    try:
        result = ai_client.complete(_LAYOUT_CLASSIFIER_PROMPT + text[:3000])
        raw = result.raw_text.strip()
        import json as _json
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return WaterLayoutType.unknown
        data = _json.loads(m.group(0))
        lt = data.get("layout_type", "unknown")
        return WaterLayoutType(lt) if lt in WaterLayoutType._value2member_map_ else WaterLayoutType.unknown
    except Exception:
        return WaterLayoutType.unknown


def detect_layout(text: str, ai_client: Any = None) -> WaterLayoutType:
    """Two-stage layout detector: deterministic first, AI only when uncertain."""
    layout = detect_layout_deterministic(text)
    if layout == WaterLayoutType.unknown and ai_client is not None:
        layout = detect_layout_ai(text, ai_client)
    return layout


# ---------------------------------------------------------------------------
# Row type enum
# ---------------------------------------------------------------------------

class RowType(str, Enum):
    recurring_charge = "recurring_charge"
    daily_access_charge = "daily_access_charge"
    usage_charge = "usage_charge"
    payment_or_credit = "payment_or_credit"
    arrears_or_brought_forward = "arrears_or_brought_forward"
    balance_or_total_due = "balance_or_total_due"
    metadata = "metadata"
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RawRow:
    label: str
    amount: float
    period_days: int | None = None   # parsed from date range
    is_negative: bool = False
    source: str = "unknown"          # "period_line", "daily_rate", "label_amount"
    daily_rate: float | None = None  # $/day (normalised from ¢/day if needed)


@dataclass
class ClassifiedRow:
    raw: RawRow
    row_type: RowType
    multiplier: int | None = None    # set during annualise step
    annualised: float | None = None  # set during annualise step
    excluded_reason: str | None = None
    confidence: float = 0.90         # set during annualise step from source


# Per-source parser confidence (improvement 4)
_SOURCE_CONFIDENCE: dict[str, float] = {
    "annual_column":     0.99,
    "daily_rate":        0.97,
    "gippsland_stacked": 0.96,
    "period_line":       0.94,
    "label_amount":      0.80,
    "unknown":           0.70,
}


# ---------------------------------------------------------------------------
# Global classification patterns
# ---------------------------------------------------------------------------

# These labels ALWAYS map to exclusion regardless of authority
_EXCLUDE_PATTERNS = re.compile(
    r"(?:^|\b)"
    r"(?:"
    r"payments?|paid|receipt|receipts?"
    r"|arrears?|overdue|outstanding\s+balance"
    r"|brought\s+forward|balance\s+(?:brought\s+forward|b/?f)"
    r"|credit(?:\s+note)?|rebate|concession\s+(?:credit|rebate)"
    r"|gst\s+(?:credit|adjustment)"
    r"|refund"
    r")"
    r"(?:\b|$)",
    re.IGNORECASE,
)

# "Total amount due", "Balance due", "Subtotal", "Total outstanding" — balance/total rows
_BALANCE_PATTERNS = re.compile(
    r"(?:"
    r"subtotal\s+(?:service\s+)?charges?"
    r"|total\s+unpaid\s+balance"
    r"|total\s+(?:amount\s+)?(?:due|outstanding|payable)"
    r"|amount\s+(?:now\s+)?due"
    r"|balance\s+due"
    r"|total\s+current\s+(?:charges?|amount)"
    r"|statement\s+total"
    r"|total\s+due"
    r")",
    re.IGNORECASE,
)

# Usage-based rows (volume × rate) — never annualise
_USAGE_PATTERNS = re.compile(
    r"(?:"
    r"usage|consumption|volume|kilolitres?"
    r"|kl\s+(?:charge|rate)"
    r"|water\s+usage"
    r")",
    re.IGNORECASE,
)

# Clearly recurring fixed service/access charges
_RECURRING_PATTERNS = re.compile(
    r"(?:"
    r"service\s+charge"
    r"|access\s+charge"
    r"|availability\s+charge"
    r"|supply\s+charge"
    r"|fixed\s+charge"
    r"|connection\s+charge"
    r"|sewerage?\s+(?:service|charge)"
    r"|wastewater\s+(?:service|charge)"
    r"|water\s+(?:service|charge|supply)"
    r"|fire\s+service"
    r"|environmental\s+(?:levy|charge)"
    r"|waterways?\s+(?:and\s+drainage\s+)?charge"
    r"|drainage\s+charge"
    r"|bulk\s+entitlement"
    r"|vacant\s+land"
    r"|meter\s+(?:service|rental|charge)"
    r"|infrastructure\s+(?:levy|charge)"
    r"|system\s+(?:access|charge)"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Authority template registry
# ---------------------------------------------------------------------------

@dataclass
class AuthorityTemplate:
    """Per-authority hints that guide classification and annualisation."""
    name: str
    # Typical billing period in days (used when date range not parseable)
    default_period_days: int = 92          # quarterly
    # If True, per-line "Annual" column is present in doc (GWW-style)
    has_annual_column: bool = False
    # Labels that this authority bills annually (multiplier=1 always)
    annual_labels: tuple[str, ...] = ()
    # Labels known to be quarterly at this authority
    quarterly_labels: tuple[str, ...] = ()


_AUTHORITY_TEMPLATES: dict[str, AuthorityTemplate] = {
    "Greater Western Water": AuthorityTemplate(
        name="Greater Western Water",
        default_period_days=92,
        has_annual_column=True,
    ),
    "City West Water": AuthorityTemplate(
        name="City West Water",
        default_period_days=92,
    ),
    "Yarra Valley Water": AuthorityTemplate(
        name="Yarra Valley Water",
        default_period_days=92,
    ),
    "South East Water": AuthorityTemplate(
        name="South East Water",
        default_period_days=92,
    ),
    "Central Highlands Water": AuthorityTemplate(
        name="Central Highlands Water",
        default_period_days=92,
        # CHW uses daily rates ($/day notation)
    ),
    "Westernport Water": AuthorityTemplate(
        name="Westernport Water",
        default_period_days=92,
        annual_labels=("Waterways and Drainage Charge",),
    ),
    "Gippsland Water": AuthorityTemplate(
        name="Gippsland Water",
        default_period_days=122,   # 3 billing periods/year ≈ 122 days each
    ),
    "Barwon Water": AuthorityTemplate(
        name="Barwon Water",
        default_period_days=92,
    ),
    "Coliban Water": AuthorityTemplate(
        name="Coliban Water",
        default_period_days=92,
    ),
    "GWMWater": AuthorityTemplate(
        name="GWMWater",
        default_period_days=92,
    ),
    "North East Water": AuthorityTemplate(
        name="North East Water",
        default_period_days=92,
    ),
    "East Gippsland Water": AuthorityTemplate(
        name="East Gippsland Water",
        default_period_days=92,
    ),
    "South Gippsland Water": AuthorityTemplate(
        name="South Gippsland Water",
        default_period_days=92,
    ),
    "Wannon Water": AuthorityTemplate(
        name="Wannon Water",
        default_period_days=92,
    ),
    "Goulburn Valley Water": AuthorityTemplate(
        name="Goulburn Valley Water",
        default_period_days=92,
    ),
    "Lower Murray Water": AuthorityTemplate(
        name="Lower Murray Water",
        default_period_days=92,
    ),
    "Western Water": AuthorityTemplate(
        name="Western Water",
        default_period_days=92,
    ),
}

# Safe fallback for unknown authorities
_UNKNOWN_TEMPLATE = AuthorityTemplate(
    name="Unknown",
    default_period_days=92,
)


def _get_template(authority: str) -> AuthorityTemplate:
    for key, tmpl in _AUTHORITY_TEMPLATES.items():
        if key.lower() in authority.lower() or authority.lower() in key.lower():
            return tmpl
    return _UNKNOWN_TEMPLATE


# ---------------------------------------------------------------------------
# Known authorities (detection)
# ---------------------------------------------------------------------------

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

_AUTHORITY_ALIASES: dict[str, str] = {
    "goulburn valley region water corporation": "Goulburn Valley Water",
    "central highlands region water corporation": "Central Highlands Water",
    "grampians wimmera mallee water corporation": "GWMWater",
    "greater western water corporation": "Greater Western Water",
}

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

_DATE_NUM_PAT = r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
_DATE_ALPHA_PAT = r"\d{1,2}[\s\-][A-Za-z]{3,9}[\s\-]\d{2,4}"
_DATE_ANY = rf"(?:{_DATE_NUM_PAT}|{_DATE_ALPHA_PAT})"

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date(s: str) -> date | None:
    s = s.strip()
    # Try DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            pass
    # Try DD Mon YYYY or DD-Mon-YYYY
    m = re.match(r"(\d{1,2})[\s\-]([A-Za-z]{3,9})[\s\-](\d{2,4})$", s)
    if m:
        d = int(m.group(1))
        mo = _MONTH_MAP.get(m.group(2).lower()[:3])
        y = int(m.group(3))
        if y < 100:
            y += 2000
        if mo:
            try:
                return date(y, mo, d)
            except ValueError:
                pass
    return None


def _period_days(from_str: str, to_str: str) -> int | None:
    d1 = _parse_date(from_str)
    d2 = _parse_date(to_str)
    if d1 and d2 and d2 > d1:
        return (d2 - d1).days
    return None


# ---------------------------------------------------------------------------
# Row parsing regexes
# ---------------------------------------------------------------------------

_PERIOD_LINE_RE = re.compile(
    rf"^(?P<label>[A-Za-z][A-Za-z &/\-]{{1,60}}?)\s+"
    rf"(?P<from>{_DATE_ANY})"
    rf"(?:\s+(?:to|[-–])\s+|\s+)"
    rf"(?P<to>{_DATE_ANY})\s+"
    rf"\$?(?P<amount>[\d,]+\.\d{{2}})",
    re.MULTILINE | re.IGNORECASE,
)

_DAILY_RATE_RE = re.compile(
    rf"(?P<label>[A-Za-z][A-Za-z\s/]{{1,50}}?):\s+"
    rf"(?:[A-Za-z0-9\s:.\-]{{0,30}}?\s+)?"
    rf"[Ff]rom\s+(?P<from>{_DATE_ANY})\s+"
    rf"[Tt]o\s+(?P<to>{_DATE_ANY})"
    rf"\s*=\s*(?P<days>\d+)\s+[Dd]ays?\s+@\s+"
    rf"(?P<rate>[\d.]+)(?P<unit>[¢c])?\s+[Pp]er\s+[Dd]ay"
    rf"\s*=\s*\$(?P<total>[\d,]+\.\d{{2}})",
    re.IGNORECASE,
)

# GWW annual column: Label  $annual_amount  Frequency  $ytd  $outstanding
_GWW_ANNUAL_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z\s&/]+?)\s+"
    r"\$(?P<annual>[\d,]+\.\d{2})\s+"
    r"(?P<freq>Quarterly|Half[\s-]?yearly|Annual|Monthly)\s+"
    r"\$(?P<ytd>[\d,]+\.\d{2})\s+"
    r"\$(?P<outstanding>[\d,]+\.\d{2})\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_TOTAL_ANNUAL_RE = re.compile(
    r"Total\s+annual\s+charges?\s+\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

_ANNUAL_LABEL_RE = re.compile(
    r"(?:"
    r"annual\s+(?:rates?|charges?|amount|fees?|service\s+fees?)"
    r"|total\s+annual"
    r"|annual\s+total"
    r")"
    r"[^\n$]{0,50}\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

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

_USAGE_LINE_RE = re.compile(
    r"(?P<volume>[\d]+(?:\.\d+)?)\s*kL?\s*@\s*"
    r"\$?(?P<rate>[\d.]+)(?P<unit>[¢c])?\s*(?:/\s*kL?|per\s+kL?)",
    re.IGNORECASE,
)

_AUTHORITY_RE = re.compile(
    r"(?:" + "|".join(re.escape(a) for a in _KNOWN_AUTHORITIES) + r")",
    re.IGNORECASE,
)

_CERT_NO_RE = re.compile(
    r"(?:Information Statement|Rate Certificate No\.?:?|Certificate No\.?:?)\s*[:#]?\s*(\d{6,})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Multiplier logic
# ---------------------------------------------------------------------------

def multiplierFromPeriod(days: int) -> int | None:
    """Return annualisation multiplier from billing period in days.

    80–100  → ×4  (quarterly)
    110–140 → ×3  (tri-annual / Gippsland)
    170–190 → ×2  (half-yearly)
    350–380 → ×1  (annual)
    """
    if 80 <= days <= 100:
        return 4
    if 110 <= days <= 140:
        return 3
    if 170 <= days <= 190:
        return 2
    if 350 <= days <= 380:
        return 1
    return None


# ---------------------------------------------------------------------------
# Row classifier
# ---------------------------------------------------------------------------

def is_safe_period_charge_row(row: RawRow, label: str) -> bool:
    """Return True if the row is structurally a recurring authority charge.

    A row is safe to include when it has a parseable date range that maps to
    a known billing period (quarterly / tri-annual / half-yearly / annual)
    AND none of the global exclusion patterns match.  This handles arbitrary
    charge names — Parks Fee, Drainage Fee, Waterways Levy, etc. — without
    needing to enumerate every label.
    """
    label_l = label.lower()
    if _EXCLUDE_PATTERNS.search(label_l):
        return False
    if _BALANCE_PATTERNS.search(label_l):
        return False
    if _USAGE_PATTERNS.search(label_l):
        return False
    if row.amount <= 0:
        return False
    # Structure: row came from a date-range line AND the period maps to a known multiplier
    if row.source == "period_line" and row.period_days and multiplierFromPeriod(row.period_days):
        return True
    return False


def classifyRow(row: RawRow, template: AuthorityTemplate) -> RowType:
    """Deterministic classification. Returns RowType."""
    label = row.label
    label_lower = label.lower().strip()

    # 0. Non-positive amounts are never a charge
    if row.amount <= 0:
        return RowType.payment_or_credit

    # 1. Exclusions — payments, credits, arrears (NEVER annual amount)
    if _EXCLUDE_PATTERNS.search(label_lower):
        return RowType.payment_or_credit

    # 2. Balance / total due rows
    if _BALANCE_PATTERNS.search(label_lower):
        return RowType.balance_or_total_due

    # 3. Usage-based rows
    if _USAGE_PATTERNS.search(label_lower):
        return RowType.usage_charge

    # 4. Daily-rate rows (Central Highlands $/day notation) — classify before
    #    period-line check because their period_days (e.g. 51) won't map to a
    #    multiplier and would otherwise fall through to unknown.
    if row.source == "daily_rate" and row.daily_rate is not None:
        return RowType.daily_access_charge

    # 5. Structure-based recurring rule (primary, non-hardcoded):
    #    any period_line row with a known billing period is recurring
    if is_safe_period_charge_row(row, label):
        return RowType.recurring_charge

    # 6. Label-pattern recurring (backup for plain label+amount rows)
    if _RECURRING_PATTERNS.search(label_lower):
        return RowType.recurring_charge

    # 7. Per-authority annual labels (always annual, multiplier=1)
    for annual_lbl in template.annual_labels:
        if annual_lbl.lower() in label_lower or label_lower in annual_lbl.lower():
            return RowType.recurring_charge

    # 8. Metadata-like rows
    if re.search(r"(?:gst|tax|levy|rate\s+in\s+dollar|valuation|notice|certificate)", label_lower):
        if not _RECURRING_PATTERNS.search(label_lower):
            return RowType.metadata

    return RowType.unknown


# ---------------------------------------------------------------------------
# Annualisation
# ---------------------------------------------------------------------------

def annualiseRow(
    row: RawRow,
    row_type: RowType,
    template: AuthorityTemplate,
    statement_year: int | None = None,
) -> ClassifiedRow:
    """Compute annualised amount for recurring / daily rows."""
    cr = ClassifiedRow(raw=row, row_type=row_type)
    cr.confidence = _SOURCE_CONFIDENCE.get(row.source, 0.70)

    if row_type == RowType.daily_access_charge:
        days_in_year = 366 if (statement_year and calendar.isleap(statement_year)) else 365
        cr.multiplier = days_in_year
        # Prefer the stored per-day rate (exact, avoids rounding from period total).
        # Fall back to deriving from period total only if daily_rate was not parsed.
        if row.daily_rate is not None:
            cr.annualised = round(row.daily_rate * days_in_year, 2)
        else:
            period = row.period_days or template.default_period_days
            if period > 0:
                cr.annualised = round((row.amount / period) * days_in_year, 2)
        return cr

    if row_type == RowType.recurring_charge:
        # Check if label is in authority's annual_labels (multiplier=1)
        for annual_lbl in template.annual_labels:
            if annual_lbl.lower() in row.label.lower() or row.label.lower() in annual_lbl.lower():
                cr.multiplier = 1
                cr.annualised = row.amount
                return cr

        period = row.period_days or template.default_period_days
        mult = multiplierFromPeriod(period)
        if mult is None:
            # Fallback: use authority default period
            mult = multiplierFromPeriod(template.default_period_days) or 4
        cr.multiplier = mult
        cr.annualised = round(row.amount * mult, 2)
        return cr

    return cr


# ---------------------------------------------------------------------------
# Guard: can we use total_due as fallback?
# ---------------------------------------------------------------------------

def canUseTotalDueFallback(
    classified: list[ClassifiedRow],
    total_due: float | None,
    template: AuthorityTemplate,
) -> bool:
    """Only use total_due annualised if we found zero recurring charge rows."""
    if total_due is None:
        return False
    recurring = [r for r in classified if r.row_type == RowType.recurring_charge and r.annualised is not None]
    if recurring:
        return False
    # Sanity: total_due must be a plausible annual amount after annualisation
    mult = multiplierFromPeriod(template.default_period_days) or 4
    annualised = total_due * mult
    return 50.0 < annualised < 50_000.0


# ---------------------------------------------------------------------------
# Winner selection
# ---------------------------------------------------------------------------

def _looks_like_summary_row(row: ClassifiedRow, all_rows: list[ClassifiedRow]) -> bool:
    """Return True if this row is a subtotal of the other rows.

    Checks both raw amounts (pre-annualisation) and annualised amounts.
    Raw check catches subtotals where the doc shows the period sum before
    any multiplier is applied; annualised check catches rows that slipped
    through _BALANCE_PATTERNS and whose multiplied value equals the total.
    """
    others = [r for r in all_rows if r.raw.label != row.raw.label]
    if not others:
        return False
    raw_sum = round(sum(r.raw.amount for r in others), 2)
    ann_sum = round(sum(r.annualised or 0.0 for r in others), 2)
    return (
        abs(raw_sum - row.raw.amount) <= 0.05
        or abs(ann_sum - (row.annualised or 0.0)) <= 0.05
    )


def chooseWinner(classified: list[ClassifiedRow]) -> tuple[float, str, list[dict], float]:
    """Sum annualised recurring rows after removing implicit subtotals.

    Returns (annual_amount, strategy, breakdown, avg_confidence).
    """
    candidates = [
        r for r in classified
        if r.row_type in (RowType.recurring_charge, RowType.daily_access_charge)
        and r.annualised is not None
    ]

    if not candidates:
        return 0.0, "no_recurring_rows", [], 0.0

    # Remove any row whose value equals the sum of all other rows (subtotal guard)
    filtered = [r for r in candidates if not _looks_like_summary_row(r, candidates)]
    # If filtering removed everything it was a false positive — keep originals
    if not filtered:
        filtered = candidates

    total = round(sum(r.annualised for r in filtered), 2)
    avg_conf = round(sum(r.confidence for r in filtered) / len(filtered), 3)

    breakdown = [
        {
            "label": r.raw.label,
            "amount": r.raw.amount,
            "multiplier": r.multiplier,
            "annualised": r.annualised,
            "source": r.raw.source,
            "confidence": r.confidence,
        }
        for r in filtered
    ]
    return total, "recurring_sum", breakdown, avg_conf


# Plain label + amount line (no date range required)
# Matches: "Water Service Charge   $89.82"  or  "Payments   -$215.02"
# Label: 3+ chars, starts with letter, no digit runs (avoids matching date lines)
_LABEL_AMOUNT_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9 &/()\-]{2,70}?)[ \t]+"
    r"(?P<sign>-?)[ \t]*\$?(?P<amount>[\d,]+\.\d{2})[ \t]*$",
    re.MULTILINE,
)

# Skip labels that are obviously table headers or junk
_LABEL_SKIP = frozenset({
    "from", "to", "date", "period", "description", "charge", "label",
    "amount", "charges", "total", "sub total", "subtotal",
    "gst", "inc gst", "ex gst", "net", "gross",
})


def _detect_billing_period_days(text: str) -> int | None:
    """Try to detect the billing period from statement date range in the doc header."""
    # Look for "From DD/MM/YYYY To DD/MM/YYYY" or "Period: DD/MM/YYYY - DD/MM/YYYY"
    period_header_re = re.compile(
        rf"(?:Period|From|Billing\s+Period)[:\s]+(?P<from>{_DATE_ANY})"
        rf"[\s\-–toTO]+(?P<to>{_DATE_ANY})",
        re.IGNORECASE,
    )
    m = period_header_re.search(text[:3000])
    if m:
        days = _period_days(m.group("from"), m.group("to"))
        if days and 50 <= days <= 400:
            return days
    return None


# ---------------------------------------------------------------------------
# Text parsing — extract raw rows from document text
# ---------------------------------------------------------------------------

_GIPPSLAND_TRIGGER = "Gippsland Water billing periods"

# Service charges that can be recurring on a Gippsland financial statement.
# Usage, miscellaneous, adjustments, and credits are excluded by the caller.
_GIPPSLAND_SERVICE_LABELS = re.compile(
    r"(Water Service Charges|Wastewater Service Charges|Fire Service Charges)",
    re.IGNORECASE,
)

# "Gippsland Water billing periods: 3 per year" → group(1) = "3"
_GIPPSLAND_PERIODS_RE = re.compile(
    r"Gippsland Water billing periods\s*:\s*(\d+)\s*per year",
    re.IGNORECASE,
)


def _parse_gippsland_stacked(text: str) -> list[RawRow]:
    """Parse the Gippsland Water stacked financial-statement layout.

    Gippsland bills list labels and amounts in separate columns/lines inside
    an "Adjustable Charges" block.  The billing frequency is stated explicitly
    as "Gippsland Water billing periods: N per year", so we compute period_days
    as 365 // N (leap-year accuracy is not required here since the multiplier
    is read directly from the doc).

    Only service charges with amount > 0 are returned.  Usage charges,
    miscellaneous adjustments, and credits are excluded by the block scope.
    """
    if _GIPPSLAND_TRIGGER not in text:
        return []

    # Determine billing periods per year
    pm = _GIPPSLAND_PERIODS_RE.search(text)
    periods_per_year = int(pm.group(1)) if pm else 3  # Gippsland default is 3
    period_days = round(365 / periods_per_year)

    # Scope to the Adjustable Charges block to avoid picking up usage / misc rows
    block_m = re.search(
        r"Adjustable Charges:(.*?)(?:Non Adjustable Charges:|Total Outstanding)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not block_m:
        return []

    block = block_m.group(1)

    rows: list[RawRow] = []
    for m in re.finditer(
        r"(Water Service Charges|Wastewater Service Charges|Fire Service Charges)"
        r"\s+(-?\$?[\d,]+\.\d{2})",
        block,
        re.IGNORECASE,
    ):
        label = m.group(1).strip()
        amount_s = m.group(2).replace("$", "").replace(",", "")
        try:
            amount = float(amount_s)
        except ValueError:
            continue
        if amount > 0:
            rows.append(RawRow(
                label=label,
                amount=amount,
                period_days=period_days,
                is_negative=False,
                source="gippsland_stacked",
            ))
    return rows


def _extract_raw_rows(
    text: str,
    layout: WaterLayoutType = WaterLayoutType.unknown,
) -> list[RawRow]:
    rows: list[RawRow] = []
    seen_labels: set[str] = set()

    # Gippsland stacked layout — dedicated parser, skip all generic parsers
    if _GIPPSLAND_TRIGGER in text:
        gippsland_rows = _parse_gippsland_stacked(text)
        if gippsland_rows:
            return gippsland_rows

    # Detect document-level billing period (used when rows have no date ranges)
    doc_period_days = _detect_billing_period_days(text)

    # Layout hint: daily_rate_lines docs should only use the daily-rate parser
    # to avoid plain label+amount lines (running totals, usage rows) polluting
    # the result. Period_row_table docs should skip the plain parser too.
    _skip_plain_parser = layout in (
        WaterLayoutType.daily_rate_lines,
        WaterLayoutType.period_row_table,
    )

    # 1. Period lines (label + date range + amount) — gives per-row period_days
    for m in _PERIOD_LINE_RE.finditer(text):
        label = m.group("label").strip()
        if label.lower() in _LABEL_SKIP:
            continue
        amount_str = m.group("amount").replace(",", "")
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        days = _period_days(m.group("from"), m.group("to"))
        key = label.lower()
        if key not in seen_labels:
            seen_labels.add(key)
            rows.append(RawRow(
                label=label,
                amount=amount,
                period_days=days,
                is_negative=amount < 0,
                source="period_line",
            ))

    # 2. Daily rate lines (Central Highlands style)
    for m in _DAILY_RATE_RE.finditer(text):
        label = m.group("label").strip()
        amount_str = m.group("total").replace(",", "")
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        days = int(m.group("days"))
        try:
            rate = float(m.group("rate"))
            unit = (m.group("unit") or "").lower()
            if unit in ("c", "¢"):
                rate = rate / 100.0
        except (ValueError, AttributeError):
            rate = amount / days if days else None
        key = label.lower()
        if key not in seen_labels:
            seen_labels.add(key)
            rows.append(RawRow(
                label=label,
                amount=amount,
                period_days=days,
                is_negative=False,
                source="daily_rate",
                daily_rate=rate,
            ))

    # 3. Plain label + amount lines (most common format — no date ranges).
    #    Skipped when layout is known to use date ranges or daily-rate notation,
    #    because plain-line parsing on those docs captures running totals / usage
    #    rows that pollute the annual amount.
    if _skip_plain_parser:
        return rows
    for m in _LABEL_AMOUNT_RE.finditer(text):
        label = m.group("label").strip()
        if label.lower() in _LABEL_SKIP:
            continue
        # Skip rows where the label itself contains a date — these are period-range
        # rows already captured by _PERIOD_LINE_RE; matching them here would
        # double-count e.g. "Water Service Charge 01/04/2026 to 30/06/2026 $21.97"
        if re.search(_DATE_ANY, label):
            continue
        # Skip explicit subtotals that slipped past _BALANCE_PATTERNS
        if re.search(r"\bsubtotal\b", label, re.IGNORECASE):
            continue
        # Skip if label starts with digit or is very short
        if re.match(r"^\d", label) or len(label) < 4:
            continue
        raw_amount = m.group("amount").replace(",", "")
        try:
            amount = float(raw_amount)
        except ValueError:
            continue
        if m.group("sign") == "-":
            amount = -amount
        key = label.lower()
        if key not in seen_labels:
            seen_labels.add(key)
            rows.append(RawRow(
                label=label,
                amount=amount,
                period_days=doc_period_days,
                is_negative=amount < 0,
                source="label_amount",
            ))

    return rows


def _extract_gww_annual_rows(text: str) -> list[tuple[str, float]]:
    """Extract (label, annual_amount) pairs from GWW annual column."""
    result = []
    for m in _GWW_ANNUAL_LINE_RE.finditer(text):
        label = m.group("label").strip()
        try:
            annual = float(m.group("annual").replace(",", ""))
            result.append((label, annual))
        except ValueError:
            pass
    return result


def _detect_authority(text: str) -> str:
    for alias, canonical in _AUTHORITY_ALIASES.items():
        if alias in text.lower():
            return canonical
    for auth in _KNOWN_AUTHORITIES:
        if auth.lower() in text.lower():
            return auth
    return "Unknown"


def _detect_statement_year(text: str) -> int | None:
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# AI row classification (for unknown rows)
# ---------------------------------------------------------------------------

def _build_ai_classification_prompt(unknown_rows: list[RawRow], authority: str) -> str:
    rows_json = "\n".join(
        f'  {{"label": {r.label!r}, "amount": {r.amount}}}' for r in unknown_rows
    )
    return f"""You are classifying line items from a {authority} water rates document.

For each row below, output ONLY a JSON array with the same order:
[
  {{"label": "...", "row_type": "<type>"}},
  ...
]

Valid row_type values:
- "recurring_charge"          fixed service or access charge billed each period
- "daily_access_charge"       charge expressed as $/day
- "usage_charge"              volume-based charge (kL usage)
- "payment_or_credit"         payment received, credit note, rebate
- "arrears_or_brought_forward" overdue or carried-forward balance
- "balance_or_total_due"      total outstanding or amount due
- "metadata"                  non-monetary info (dates, property ID, etc.)
- "unknown"                   genuinely unclear

Rows to classify:
{rows_json}

CRITICAL RULES:
- Payments / credits / arrears must NEVER be classified as recurring_charge
- Negative amounts are almost always payment_or_credit or arrears
- Return ONLY the JSON array, no explanation.
"""


def _call_ai_classify(unknown_rows: list[RawRow], authority: str, ai_client: Any) -> dict[str, RowType]:
    """Returns label → RowType mapping for rows AI classified."""
    if not unknown_rows or ai_client is None:
        return {}

    prompt = _build_ai_classification_prompt(unknown_rows, authority)
    try:
        result = ai_client.complete(prompt)
        raw = result.raw_text.strip()
        # Extract JSON array
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            return {}
        import json
        items = json.loads(m.group(0))
        mapping: dict[str, RowType] = {}
        for item in items:
            lbl = item.get("label", "")
            rt_str = item.get("row_type", "unknown")
            try:
                mapping[lbl] = RowType(rt_str)
            except ValueError:
                mapping[lbl] = RowType.unknown
        return mapping
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Property-block parsing
# ---------------------------------------------------------------------------

@dataclass
class PropertyBlock:
    address: str
    lot_plan: str | None
    property_number: str | None
    property_type: str | None
    text: str
    is_primary: bool = False
    is_related: bool = False


# Matches a property header section (up to 4 lines for wrapped addresses)
# followed by the charges body.
# Stops at the next property header, "GENERAL MANAGER", or end of text.
_PROPERTY_HEADER_SENTINEL = re.compile(
    r"Property Address\s+Lot\s*&\s*Plan\s+Property Number\s+Property Type\s*\n",
    re.IGNORECASE,
)

_PROPERTY_BLOCK_TERMINATOR = re.compile(
    r"\nGENERAL MANAGER",
    re.IGNORECASE,
)

# Matches lot/plan + property number + type fields anywhere in up to 4 header lines.
_BLOCK_LOT_RE = re.compile(
    r"(?P<lotplan>\d+[\\\/][A-Z]{0,3}\d+[A-Z]?)\s+"
    r"(?P<propnum>\d{4,})\s+"
    r"(?P<ptype>\w+)",
    re.IGNORECASE,
)


def extract_property_blocks(text: str) -> list[PropertyBlock]:
    """Split a water statement into per-property charge blocks.

    Uses a split-on-sentinel approach to avoid backtracking issues with
    complex nested regex on wrapped multi-line addresses.

    Returns blocks in document order.  The first block is marked primary;
    all subsequent blocks are marked related (master/shared property accounts).
    Returns empty list when no property header structure is found.
    """
    # Find all sentinel positions
    sentinel_spans = [(m.start(), m.end()) for m in _PROPERTY_HEADER_SENTINEL.finditer(text)]
    if not sentinel_spans:
        return []

    # Determine block boundaries: each block runs from after sentinel to next sentinel start
    term_match = _PROPERTY_BLOCK_TERMINATOR.search(text)
    text_end = term_match.start() if term_match else len(text)

    block_chunks: list[tuple[int, int, int]] = []  # (sentinel_start, content_start, content_end)
    for i, (sent_start, sent_end) in enumerate(sentinel_spans):
        content_start = sent_end
        content_end = sentinel_spans[i + 1][0] if i + 1 < len(sentinel_spans) else text_end
        block_chunks.append((sent_start, content_start, content_end))

    blocks: list[PropertyBlock] = []
    for sent_start, content_start, content_end in block_chunks:
        chunk = text[content_start:content_end]
        lines = chunk.splitlines()

        # Collect up to 4 non-empty lines as the header area (before "Agreement Type")
        header_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"Agreement Type", stripped, re.IGNORECASE):
                break
            header_lines.append(stripped)
            if len(header_lines) >= 4:
                break

        header_flat = " ".join(header_lines)
        lm = _BLOCK_LOT_RE.search(header_flat)
        if not lm:
            continue

        # Address is everything before the lot/plan token
        address = header_flat[: header_flat.index(lm.group("lotplan"))].strip()
        address = re.sub(r"\s+\d+\s*$", "", address).strip()

        block_text = text[sent_start:content_end]
        blocks.append(PropertyBlock(
            address=address,
            lot_plan=lm.group("lotplan"),
            property_number=lm.group("propnum"),
            property_type=lm.group("ptype"),
            text=block_text,
        ))

    if blocks:
        blocks[0].is_primary = True
        for b in blocks[1:]:
            b.is_related = True

    return blocks


# ---------------------------------------------------------------------------
# Extraction snapshot  (enabled by TRICONVEY_WATER_SNAPSHOTS=1)
# ---------------------------------------------------------------------------

_SNAPSHOTS_ENABLED = os.getenv("TRICONVEY_WATER_SNAPSHOTS", "0").strip() == "1"
_SNAPSHOT_DIR = Path(os.getenv("TRICONVEY_WATER_SNAPSHOT_DIR", "water_snapshots"))


def _dataclass_to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses / enums / lists to JSON-safe types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _dataclass_to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list):
        return [_dataclass_to_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


_SNAPSHOT_VERSION = 1


def _save_snapshot(
    doc_filename: str,
    *,
    authority: str,
    layout: str,
    property_blocks: list[PropertyBlock],
    scoped_to_property_block: bool = False,
    scoped_property_number: str | None = None,
    excluded_property_blocks_count: int = 0,
    raw_rows: list[RawRow],
    classified_rows: list[ClassifiedRow],
    excluded_rows: list[dict],
    winner_strategy: str,
    annual_amount: float,
    confidence: float,
    avg_parser_confidence: float,
    warnings: list[str],
) -> None:
    """Write a JSON snapshot of the full pipeline state for this document.

    Only runs when TRICONVEY_WATER_SNAPSHOTS=1 is set.  Snapshots land in
    TRICONVEY_WATER_SNAPSHOT_DIR (default: ./water_snapshots/).

    Snapshot shape (snapshot_version=1):
        snapshot_version        — bumped when shape changes
        document, authority, layout
        scoped_to_property_block / scoped_property_number / excluded_property_blocks_count
        property_blocks[]   → address / lot_plan / is_primary / is_related
        raw_rows[]          → label / amount / period_days / source / daily_rate
        classified_rows[]   → raw{} / row_type / multiplier / annualised / confidence
        excluded_rows[]     → label / amount / reason
        winner_strategy, annual_amount, confidence, avg_parser_confidence
        warnings[]
    """
    if not _SNAPSHOTS_ENABLED:
        return

    stem = Path(doc_filename).stem if doc_filename else "unknown"
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _SNAPSHOT_DIR / f"{stem}.snapshot.json"

    snapshot = {
        "snapshot_version": _SNAPSHOT_VERSION,
        "document": doc_filename,
        "authority": authority,
        "layout": layout,
        "scoped_to_property_block": scoped_to_property_block,
        "scoped_property_number": scoped_property_number,
        "excluded_property_blocks_count": excluded_property_blocks_count,
        "property_blocks": [
            {
                "address": b.address,
                "lot_plan": b.lot_plan,
                "property_number": b.property_number,
                "property_type": b.property_type,
                "is_primary": b.is_primary,
                "is_related": b.is_related,
            }
            for b in property_blocks
        ],
        "raw_rows": [
            {
                "label": r.label,
                "amount": r.amount,
                "period_days": r.period_days,
                "is_negative": r.is_negative,
                "source": r.source,
                "daily_rate": r.daily_rate,
            }
            for r in raw_rows
        ],
        "classified_rows": [
            {
                "label": cr.raw.label,
                "raw_amount": cr.raw.amount,
                "period_days": cr.raw.period_days,
                "source": cr.raw.source,
                "row_type": cr.row_type.value if isinstance(cr.row_type, Enum) else cr.row_type,
                "multiplier": cr.multiplier,
                "annualised": cr.annualised,
                "confidence": cr.confidence,
                "excluded_reason": cr.excluded_reason,
            }
            for cr in classified_rows
        ],
        "excluded_rows": excluded_rows,
        "winner_strategy": winner_strategy,
        "annual_amount": annual_amount,
        "confidence": confidence,
        "avg_parser_confidence": avg_parser_confidence,
        "warnings": warnings,
    }

    try:
        out_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        LOG.debug("Water snapshot written: %s", out_path)
    except Exception as exc:
        LOG.warning("Could not write water snapshot to %s: %s", out_path, exc)


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(
    text: str,
    authority: str,
    template: AuthorityTemplate,
    ai_client: Any = None,
    doc_filename: str = "",
) -> dict:
    warnings: list[str] = []
    needs_review = template.name == "Unknown"
    statement_year = _detect_statement_year(text)
    warnings_prefix: list[str] = []

    # ── Step 2: Property-block scoping ───────────────────────────────────
    # Split the document into per-property blocks and extract only from the
    # primary (first) block.  Related/master property blocks are excluded so
    # their commercial charges don't inflate the unit's annual amount.
    blocks = extract_property_blocks(text)
    property_block_info: dict | None = None
    excluded_property_blocks: list[dict] = []

    if blocks:
        primary = blocks[0]
        extraction_text = primary.text
        property_block_info = {
            "address": primary.address,
            "property_number": primary.property_number,
            "lot_plan": primary.lot_plan,
            "property_type": primary.property_type,
            "included": True,
        }
        warnings_prefix.append(
            f"Using primary property block: {primary.address} (#{primary.property_number})"
        )
        for rb in blocks[1:]:
            excluded_property_blocks.append({
                "address": rb.address,
                "property_number": rb.property_number,
                "reason": "related/master property block",
            })
        if excluded_property_blocks:
            warnings_prefix.append(
                f"Excluded {len(excluded_property_blocks)} related/master property block(s): "
                + ", ".join(b["address"] for b in excluded_property_blocks)
            )
    else:
        extraction_text = text

    # ── Step 3 + 4: Layout classification (on scoped text) ───────────────
    layout = detect_layout(extraction_text, ai_client)

    # Override template.has_annual_column if layout classifier is confident
    if layout == WaterLayoutType.annual_column_table and not template.has_annual_column:
        template = AuthorityTemplate(
            name=template.name,
            default_period_days=template.default_period_days,
            has_annual_column=True,
            annual_labels=template.annual_labels,
            quarterly_labels=template.quarterly_labels,
        )

    if layout == WaterLayoutType.statement_balance_only:
        warnings_prefix.append(
            "Layout: statement_balance_only — no charge breakdown visible; "
            "annual amount will use total_due fallback."
        )
        needs_review = True

    if layout == WaterLayoutType.unknown:
        warnings_prefix.append("Layout could not be determined; proceeding with all parsers.")
        needs_review = True

    # ── Step 5: Layout-specific early exits ──────────────────────────────

    # --- Strategy 1: GWW / annual_column_table (highest confidence) ---
    if template.has_annual_column:
        gww_rows = _extract_gww_annual_rows(extraction_text)
        # Filter exclusions
        valid_gww = [(lbl, amt) for lbl, amt in gww_rows if not _EXCLUDE_PATTERNS.search(lbl)]
        if valid_gww:
            total = round(sum(amt for _, amt in valid_gww), 2)
            # Cross-check with "Total annual charges"
            explicit_m = _TOTAL_ANNUAL_RE.search(extraction_text)
            if explicit_m:
                explicit = float(explicit_m.group(1).replace(",", ""))
                if abs(total - explicit) < 1.0:
                    _gww_bkd = [{"label": lbl, "amount": amt, "multiplier": 1} for lbl, amt in valid_gww]
                    _save_snapshot(doc_filename, authority=authority, layout=layout.value, property_blocks=blocks, raw_rows=[], classified_rows=[], excluded_rows=[], winner_strategy="gww_annual_column_verified", annual_amount=explicit, confidence=0.99, avg_parser_confidence=0.99, warnings=warnings_prefix)
                    return {
                        "authority": authority,
                        "annual_amount": explicit,
                        "confidence": 0.99,
                        "needs_review": False,
                        "layout": layout.value,
                        "strategy": "gww_annual_column_verified",
                        "breakdown": _gww_bkd,
                        "excluded_rows": [],
                        "warnings": warnings_prefix,
                    }
            _gww_bkd = [{"label": lbl, "amount": amt, "multiplier": 1} for lbl, amt in valid_gww]
            _save_snapshot(doc_filename, authority=authority, layout=layout.value, property_blocks=blocks, raw_rows=[], classified_rows=[], excluded_rows=[], winner_strategy="gww_annual_column", annual_amount=total, confidence=0.95, avg_parser_confidence=0.95, warnings=warnings_prefix)
            return {
                "authority": authority,
                "annual_amount": total,
                "confidence": 0.95,
                "needs_review": False,
                "layout": layout.value,
                "strategy": "gww_annual_column",
                "breakdown": _gww_bkd,
                "excluded_rows": [],
                "warnings": warnings_prefix,
            }

    # --- Strategy 2: Explicit "Total annual charges" in text ---
    explicit_m = _TOTAL_ANNUAL_RE.search(extraction_text) or _ANNUAL_LABEL_RE.search(extraction_text)
    if explicit_m:
        explicit_annual = float(explicit_m.group(1).replace(",", ""))
        if 50.0 < explicit_annual < 50_000.0:
            _save_snapshot(doc_filename, authority=authority, layout=layout.value, property_blocks=blocks, raw_rows=[], classified_rows=[], excluded_rows=[], winner_strategy="explicit_annual", annual_amount=explicit_annual, confidence=0.99, avg_parser_confidence=0.99, warnings=warnings_prefix)
            return {
                "authority": authority,
                "annual_amount": explicit_annual,
                "confidence": 0.99,
                "needs_review": needs_review,
                "layout": layout.value,
                "strategy": "explicit_annual",
                "breakdown": [],
                "excluded_rows": [],
                "warnings": warnings_prefix,
            }

    # --- Strategy 3: Row classifier pipeline (layout-routed) ---
    raw_rows = _extract_raw_rows(extraction_text, layout=layout)
    classified: list[ClassifiedRow] = []
    excluded_rows: list[dict] = []
    unknown_rows: list[RawRow] = []

    for rr in raw_rows:
        rt = classifyRow(rr, template)
        if rt == RowType.unknown:
            unknown_rows.append(rr)
        elif rt in (RowType.payment_or_credit, RowType.arrears_or_brought_forward):
            excluded_rows.append({
                "label": rr.label,
                "amount": rr.amount,
                "reason": rt.value,
            })
        elif rt == RowType.balance_or_total_due:
            # Keep for potential fallback, don't add to classified yet
            excluded_rows.append({
                "label": rr.label,
                "amount": rr.amount,
                "reason": "balance_or_total_due (not used unless fallback)",
            })
        elif rt in (RowType.usage_charge, RowType.metadata):
            excluded_rows.append({
                "label": rr.label,
                "amount": rr.amount,
                "reason": rt.value,
            })
        else:
            cr = annualiseRow(rr, rt, template, statement_year)
            classified.append(cr)

    # AI classifies unknown rows
    if unknown_rows and ai_client is not None:
        ai_mapping = _call_ai_classify(unknown_rows, authority, ai_client)
        for rr in unknown_rows:
            rt = ai_mapping.get(rr.label, RowType.unknown)
            if rt in (RowType.payment_or_credit, RowType.arrears_or_brought_forward, RowType.usage_charge, RowType.metadata, RowType.balance_or_total_due):
                excluded_rows.append({"label": rr.label, "amount": rr.amount, "reason": rt.value + " (ai)"})
            elif rt in (RowType.recurring_charge, RowType.daily_access_charge):
                cr = annualiseRow(rr, rt, template, statement_year)
                classified.append(cr)
            else:
                # Still unknown after AI → EXCLUDE, never auto-promote to recurring
                excluded_rows.append({
                    "label": rr.label,
                    "amount": rr.amount,
                    "reason": "unknown_after_ai",
                })
                needs_review = True
                warnings.append(f"Unknown row excluded from annual amount: '{rr.label}'")
    else:
        # No AI: unknown rows must NOT be included in annual amount
        for rr in unknown_rows:
            excluded_rows.append({
                "label": rr.label,
                "amount": rr.amount,
                "reason": "unknown_needs_review",
            })
            needs_review = True
            warnings.append(f"Row '{rr.label}' is unknown; excluded pending review.")

    annual_amount, strategy, breakdown, avg_conf = chooseWinner(classified)

    # Low average parser confidence → flag for review
    if avg_conf > 0.0 and avg_conf < 0.85:
        needs_review = True
        warnings.append(f"Low average parser confidence ({avg_conf:.2f}); result flagged for review.")

    # --- Strategy 4: total_due fallback (guarded) ---
    if annual_amount == 0.0:
        total_due_m = _TOTAL_DUE_RE.search(extraction_text)
        total_due = float(total_due_m.group(1).replace(",", "")) if total_due_m else None
        if canUseTotalDueFallback(classified, total_due, template):
            mult = multiplierFromPeriod(template.default_period_days) or 4
            annual_amount = round(total_due * mult, 2)
            strategy = "total_due_annualised_fallback"
            needs_review = True
            warnings.append(
                f"No recurring rows found; used total_due={total_due} × {mult} as fallback."
            )
        else:
            warnings.append("No recurring rows and no valid total_due fallback found.")
            needs_review = True

    # Final confidence: prefer avg_conf from rows, fall back to heuristic
    if avg_conf > 0.0:
        confidence = round(avg_conf * (0.95 if not needs_review else 0.85), 3)
    else:
        confidence = 0.90 if strategy == "recurring_sum" and not needs_review else 0.75

    # Extraction trace — used for debugging bad results
    row_type_counts: dict[str, int] = {}
    for rr in raw_rows:
        rt = classifyRow(rr, template).value
        row_type_counts[rt] = row_type_counts.get(rt, 0) + 1

    trace = {
        "layout": layout.value,
        "rows_extracted": sum(row_type_counts.values()),
        "rows_classified": row_type_counts,
        "strategy": strategy,
        "annual_amount": annual_amount,
        "avg_parser_confidence": avg_conf,
        "property_block": property_block_info,
        "excluded_property_blocks": excluded_property_blocks,
    }

    all_warnings = warnings_prefix + warnings
    _save_snapshot(
        doc_filename,
        authority=authority,
        layout=layout.value,
        property_blocks=blocks,
        scoped_to_property_block=bool(blocks),
        scoped_property_number=blocks[0].property_number if blocks else None,
        excluded_property_blocks_count=len(blocks) - 1 if blocks else 0,
        raw_rows=raw_rows,
        classified_rows=classified,
        excluded_rows=excluded_rows,
        winner_strategy=strategy,
        annual_amount=annual_amount,
        confidence=confidence,
        avg_parser_confidence=avg_conf,
        warnings=all_warnings,
    )

    return {
        "authority": authority,
        "annual_amount": annual_amount,
        "confidence": confidence,
        "needs_review": needs_review,
        "layout": layout.value,
        "strategy": strategy,
        "breakdown": breakdown,
        "excluded_rows": excluded_rows,
        "warnings": all_warnings,
        "property_block": property_block_info,
        "excluded_property_blocks": excluded_property_blocks,
        "trace": trace,
    }


# ---------------------------------------------------------------------------
# Statement date extraction
# ---------------------------------------------------------------------------

_STMT_DATE_OF_ISSUE_RE = re.compile(
    r"Date\s+of\s+Issue\s+(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})",
    re.IGNORECASE,
)
_STMT_DATE_LABEL_RE = re.compile(
    r"(?:Statement\s+Date|Issue\s+Date|Date\s+of\s+(?:Issue|Statement|Certification|Preparation|Certificate)"
    r"|Dated?|Generated|Produced|Report\s+Date|Date\s+Prepared)"
    r"\s*[:\-]?\s*"
    r"(?P<date>\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
    r"|\d{1,2}[\s\-][A-Za-z]{3,9}[\s\-]\d{2,4})",
    re.IGNORECASE,
)
_STMT_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "sept": "09", "oct": "10", "nov": "11", "dec": "12",
}
_STMT_DDMMYYYY_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_STMT_DMY_WORD_RE = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{4})\b",
    re.IGNORECASE,
)


def _stmt_parse_alpha_date(raw: str) -> str | None:
    """Parse 'DD Mon YYYY' or 'DD-Mon-YYYY' → 'DD/MM/YYYY'."""
    raw = raw.strip()
    parts = re.split(r"[\s\-]", raw)
    if len(parts) == 3:
        try:
            d = int(parts[0])
            mon = _STMT_MONTH_MAP.get(parts[1].lower())
            y = int(parts[2])
            if mon and 1 <= d <= 31 and 2000 <= y <= 2040:
                return f"{d:02d}/{mon}/{y}"
        except (ValueError, IndexError):
            pass
    return None


def _stmt_normalise_date(raw: str) -> str | None:
    raw = raw.strip()
    # Numeric DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})$", raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        if 2000 <= y <= 2040:
            return f"{d:02d}/{mo:02d}/{y}"
    return _stmt_parse_alpha_date(raw)


def _extract_statement_date(text: str) -> str | None:
    """Extract statement/issue date from a water authority document."""
    # Priority 1: classic "Date of Issue DD Month YYYY"
    m = _STMT_DATE_OF_ISSUE_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        num = _STMT_MONTH_MAP.get(mon.lower())
        if num:
            return f"{int(d):02d}/{num}/{y}"

    # Priority 2: labelled date (Statement Date:, Issue Date:, Generated:, etc.)
    for m2 in _STMT_DATE_LABEL_RE.finditer(text):
        result = _stmt_normalise_date(m2.group("date"))
        if result:
            return result

    # Priority 3: plain word date "16 March 2026"
    m3 = _STMT_DMY_WORD_RE.search(text)
    if m3:
        d, mon, y = m3.group(1), m3.group(2), m3.group(3)
        num = _STMT_MONTH_MAP.get(mon.lower())
        if num:
            return f"{int(d):02d}/{num}/{y}"

    # Priority 4: first DD/MM/YYYY in 2020-2035 range
    for m4 in _STMT_DDMMYYYY_RE.finditer(text):
        ds = m4.group(1)
        year = int(ds.split("/")[2])
        if 2020 <= year <= 2035:
            return ds

    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_water_authority_certificate_facts_v2(
    doc: Document,
    *,
    ai_client: Any = None,
) -> list[Fact]:
    """Extract water authority annual rates from a water information statement.

    Returns a list of Fact objects ready for the canonical pipeline.

    Parameters
    ----------
    doc:
        The Document to process.
    ai_client:
        Optional AI client (satisfies AIClient protocol) for classifying
        ambiguous rows.  If None, unknown rows are assumed recurring and
        flagged for review.
    """
    text = doc.raw_text or doc.normalized_text or ""
    if not text.strip():
        return []

    # Quick gate — same triggers as v1 to avoid running on non-water docs
    _WATER_TRIGGERS = (
        "Water Information Statement",
        "Rate Certificate",
        "Information Statement Certificate",
        "Water Rates",
        "Rates Certificate",
        "Water Act 1989",
        "Section 158",
    )
    if not any(t.lower() in text.lower() for t in _WATER_TRIGGERS):
        # Also check authority names as triggers
        if not any(a.lower() in text.lower() for a in _KNOWN_AUTHORITIES):
            return []

    authority = _detect_authority(text)
    template = _get_template(authority)

    result = _run_pipeline(text, authority=authority, template=template, ai_client=ai_client, doc_filename=doc.filename or "")

    def _make_fact(path: str, value: object, confidence: float, notes: str | None = None) -> Fact:
        return Fact(
            path=path,
            value=value,
            confidence=confidence,
            sources=[Source(file=doc.filename, page=None, quote=None)],
            extractor=EXTRACTOR_NAME,
            notes=notes,
        )

    facts: list[Fact] = []
    annual = result.get("annual_amount", 0.0)
    if annual and annual > 0:
        strategy_note = result.get("strategy", "")
        if result.get("needs_review"):
            strategy_note += " [needs_review]"
        facts.append(_make_fact(P.RATES_WATER_ANNUAL, annual, result.get("confidence", 0.75), strategy_note or None))

    # Authority name fact
    if authority and authority != "Unknown":
        facts.append(_make_fact(P.RATES_WATER_AUTHORITY, authority, 0.95))

    # Unit number — detected from primary property block address (e.g. "Unit 3 / 12 Example St")
    _UNIT_RE = re.compile(
        r"(?:UNIT\s+)?(\d+)\s*/\s*\d+",
        re.IGNORECASE,
    )
    prop_block = result.get("property_block") or {}
    address = prop_block.get("address") or ""
    if not address:
        # Fall back to scanning raw text for address line
        addr_m = re.search(r"(?:Property\s+Address|Address)[:\s]+(.+)", text, re.IGNORECASE)
        if addr_m:
            address = addr_m.group(1).strip()
    if address:
        um = _UNIT_RE.search(address)
        if um:
            unit_no = um.group(1)
            if unit_no and int(unit_no) > 0:
                facts.append(_make_fact(
                    P.RATES_WATER_UNIT_NUMBER, unit_no, 0.95,
                    f"Unit {unit_no} detected from address in water authority document (v2)",
                ))

    # Certificate number
    cert_m = _CERT_NO_RE.search(text)
    if cert_m:
        facts.append(_make_fact(P.RATES_WATER_CERTIFICATE_NUMBER, cert_m.group(1), 0.90))

    # Certificate / statement issue date
    date_str = _extract_statement_date(text)
    if date_str:
        facts.append(_make_fact(P.DOCS_WATER_CERT_DATE, date_str, 0.92,
                                "water statement issue date (v2)"))

    return facts
