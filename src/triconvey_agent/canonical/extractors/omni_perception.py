"""Omni Perception Sub-Agent — shared table normalizer for all extractor types.

Architecture
------------
PDF / OCR / messy text
    ↓
classify_table_with_omni()   ← this module
    ↓  (AI returns DocumentTables JSON — never a final amount)
List[OmniMoneyRow]           ← each row has typed MoneyCell amounts
    ↓
annual_from_omni_rows()      ← deterministic calculator, no AI involved
    ↓
_Candidate added as source="omni_structured_rows"
    ↓
Consensus resolver → final amount / needs_review

Design rules
------------
- AI is a TABLE NORMALIZER only.  It classifies rows and cells; it never
  computes the final amount.
- Every amount gets a ``meaning`` label (current_charge, annual_total, balance …).
- The deterministic calculator ignores adjustments, interest, payments, balance,
  certificate_fee, and valuation amounts unless a specific fallback rule applies.
- The prompt always requests the same DocumentTables schema, so the AI output
  is stable and machine-readable regardless of the council/water/OC layout.

Schema returned by AI
---------------------
{
  "document_type": "council_rates",
  "authority_name": "Wyndham City Council",
  "tables": [
    {
      "table_name": "Current Year's Rates",
      "rows": [
        {
          "label": "General VRL Rates",
          "row_type": "charge",
          "amounts": [{"value": 454.28, "raw": "$454.28", "column": "amount",
                       "meaning": "current_charge"}],
          "evidence": "General VRL Rates $454.28"
        },
        {
          "label": "Current Rates Levied",
          "row_type": "annual_total",
          "amounts": [{"value": 665.24, "raw": "$665.24", "column": "amount",
                       "meaning": "annual_total"}],
          "evidence": "Current Rates Levied $665.24"
        }
      ]
    }
  ]
}

Deterministic calculator priority
----------------------------------
1. row_type == "annual_total"  →  use the annual_total / levied cell
2. sum of row_type == "charge" cells whose meaning == "current_charge" or "levied"
3. reconstruction: payments + balance + rebates  (last resort)

Ignored meanings
----------------
adjustment, interest, payment, rebate, balance, outstanding,
valuation, certificate_fee, unknown  (unless explicitly needed by fallback)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from triconvey_agent.ai.client import AIClient

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROW_TYPES = frozenset({
    "charge",
    "annual_total",
    "payment",
    "rebate",
    "adjustment",
    "interest",
    "balance",
    "valuation",
    "certificate_fee",
    "ignore",
    "unknown",
})

CELL_MEANINGS = frozenset({
    "current_charge",
    "annual_total",
    "levied",
    "payment",
    "rebate",
    "adjustment",
    "interest",
    "balance",
    "outstanding",
    "valuation",
    "certificate_fee",
    "unknown",
})

# Meanings that should NEVER be used as the annual amount
_IGNORED_MEANINGS = frozenset({
    "payment", "rebate", "adjustment", "interest",
    "balance", "outstanding", "valuation", "certificate_fee",
})

# Row types whose cells contribute to annual amount
_CHARGE_ROW_TYPES = frozenset({"charge"})

# Meanings treated as "this IS the annual amount"
_ANNUAL_MEANINGS = frozenset({"current_charge", "annual_total", "levied"})

# Sources that mean Omni should NOT be called (rule already has strong evidence)
STRONG_STRUCTURAL_SOURCES = frozenset({
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
    "date_levied_verified_by_paid_due",
    "zero_adjustments_total_due",
    # water-specific
    "explicit_annual",
    "gww_annual_column",
    # OC-specific
    "cert_fee_block",
})

# Legacy column-meaning → new cell-meaning mapping
_LEGACY_MEANING_MAP = {
    "levied": "levied",
    "current": "current_charge",
    "charge": "current_charge",
    "annual": "annual_total",
    "total": "annual_total",
    "rebate": "rebate",
    "payment": "payment",
    "balance": "balance",
    "outstanding": "outstanding",
    "due": "balance",
    "unknown": "unknown",
}

_DEBUG_LOG_PATH = Path.cwd() / "rates_omni_debug.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MoneyCell:
    """One amount on a row — always carries its classification labels."""
    value: float
    raw: str        # exact string from document, e.g. "$454.28"
    column: str     # column header name, e.g. "Levied", "Balance", "amount"
    meaning: str    # one of CELL_MEANINGS


@dataclass
class OmniMoneyRow:
    """One row extracted by the AI table normalizer."""
    label: str
    cells: list[MoneyCell]
    row_type: str       # one of ROW_TYPES
    evidence: str

    # Backward-compat views — callers that used .amounts / .column_meanings still work
    @property
    def amounts(self) -> list[float]:
        return [c.value for c in self.cells]

    @property
    def column_meanings(self) -> list[str]:
        return [c.meaning for c in self.cells]


# ---------------------------------------------------------------------------
# Unified AI prompt — same DocumentTables schema for all doc types
# ---------------------------------------------------------------------------

_BASE_PROMPT = """\
You are a document table normalizer for Australian conveyancing documents.

Your ONLY job is to convert the document's rate/charge tables into a \
machine-readable JSON structure.
Do NOT calculate or guess the final annual amount.
Do NOT invent amounts. Use only dollar values visible in the supplied text.

Return ONLY valid JSON matching this exact schema (no extra keys, no markdown):
{
  "document_type": "council_rates|water_authority|owners_corporation|land_tax|unknown",
  "authority_name": "string or null",
  "tables": [
    {
      "table_name": "string describing which section this table is from",
      "rows": [
        {
          "label": "exact label from document",
          "row_type": "charge|annual_total|payment|rebate|adjustment|interest|balance|valuation|certificate_fee|ignore|unknown",
          "amounts": [
            {
              "value": 0.00,
              "raw": "$0.00",
              "column": "column header name or 'amount' if single column",
              "meaning": "current_charge|annual_total|levied|payment|rebate|adjustment|interest|balance|outstanding|valuation|certificate_fee|unknown"
            }
          ],
          "evidence": "short verbatim quote from the document"
        }
      ]
    }
  ]
}

ROW TYPE RULES — apply first match:

COUNCIL RATES / LAND INFORMATION CERTIFICATE:
  charge         -> General rates, municipal charge, waste/garbage charge, recycling,
                    fire services levy, ESVF, emergency services levy, infrastructure levy.
                    meaning = "current_charge" for all charge cells.
  annual_total   -> "Current Rates Levied", "Total Charges", "Rates & Charges Sub Total",
                    "Current Years Rates and Charges Sub Total", "Sub Total", "Current Total".
                    meaning = "annual_total" for the total cell.
  payment        -> "Less Payments", "Receipts", "Amount Paid", "Credit Applied".
                    meaning = "payment".
  rebate         -> "Pensioner Rebate", "Concession", "Discount".
                    meaning = "rebate".
  adjustment     -> "Adjustments", "Rate Adjustment".
                    meaning = "adjustment". NEVER "current_charge".
  interest       -> "Interest", "Rate Arrears Interest".
                    meaning = "interest".
  balance        -> "Balance Outstanding", "Total Outstanding", "Balance Due",
                    "Amount Due", "Total Due", "Net Amount Due", "TOTAL OUTSTANDING".
                    meaning = "balance" or "outstanding".
  valuation      -> "Capital Improved Value", "Site Value", "Net Annual Value".
                    meaning = "valuation".
  certificate_fee-> "Certificate Fee", "Search Fee", "Admin Fee".

WATER AUTHORITY:
  charge         -> Service fees (water/sewerage/drainage), usage charges,
                    bulk water, environmental levy. meaning = "current_charge".
  annual_total   -> "Total Current Charges", "Total Annual Charges" when full-year.
  balance        -> "Amount Due", "Total Due", settlement/outstanding amounts.

OWNERS CORPORATION:
  charge         -> Administration fund levy, maintenance fund levy, sinking fund levy,
                    insurance levy. meaning = "current_charge".
  annual_total   -> "Annual Fee", "Total Annual OC Fees", "Total Levy".

COLUMN RULES (multi-column tables):
  - "Levied / Balance": first col = "levied" meaning, second = "balance" meaning.
  - "Amounts Levied / Outstanding Amount": first = "levied", second = "outstanding".
  - "Levied / Rebates / Balance": col1="levied", col2="rebate", col3="balance".
  - Matrix with a "Total" column on "Current Rates Levied" row:
    that total cell gets meaning = "annual_total".

ALWAYS:
  - Preserve exact dollar values. Do not round or alter amounts.
  - Include $0.00 rows — classify them correctly, do not omit them.
  - If a row has multiple columns, include one amount object per column.
  - If unsure about row_type, use "unknown". If unsure about meaning, use "unknown".
"""

_COUNCIL_ADDENDUM = """
DOCUMENT TYPE: council_rates — Land Information Certificate or Rates Notice.
Include every row from the rates/charges table, including totals and balance rows.
"""

_WATER_ADDENDUM = """
DOCUMENT TYPE: water_authority — Water Information Statement (Section 158).
Note in table_name or evidence whether amounts appear to be daily, quarterly, or annual.
Do NOT annualise — return amounts exactly as shown in the document.
"""

_OC_ADDENDUM = """
DOCUMENT TYPE: owners_corporation — OC search report or certificate.
Note in evidence whether amounts are annual, quarterly, or monthly.
Do NOT annualise.
"""

_ADDENDA = {
    "council": _COUNCIL_ADDENDUM,
    "water": _WATER_ADDENDUM,
    "oc": _OC_ADDENDUM,
}


def _build_prompt(doc_type: str, text: str) -> str:
    addendum = _ADDENDA.get(doc_type, _COUNCIL_ADDENDUM)
    return (
        _BASE_PROMPT
        + addendum
        + "\n\nDOCUMENT TEXT:\n"
        + text[:12_000]
    )


# ---------------------------------------------------------------------------
# JSON parser — handles DocumentTables schema and legacy {"rows":[...]} schema
# ---------------------------------------------------------------------------


def _extract_json_object(raw: str) -> dict | None:
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(raw[start: end + 1])
    except json.JSONDecodeError:
        return None


def _to_float(value: object) -> float | None:
    try:
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
        return round(float(value), 2)  # type: ignore[arg-type]
    except Exception:
        return None


def _parse_cell(cell_obj: object, fallback_column: str = "amount") -> MoneyCell | None:
    """Parse one amount entry from the AI response into a MoneyCell."""
    if isinstance(cell_obj, (int, float)):
        v = _to_float(cell_obj)
        if v is None:
            return None
        return MoneyCell(value=v, raw=str(cell_obj), column=fallback_column, meaning="unknown")

    if isinstance(cell_obj, dict):
        v = _to_float(cell_obj.get("value"))
        if v is None:
            return None
        raw_str = str(cell_obj.get("raw", f"${v:,.2f}")).strip()
        column = str(cell_obj.get("column", fallback_column)).strip() or fallback_column
        meaning = str(cell_obj.get("meaning", "unknown")).strip().lower()
        if meaning not in CELL_MEANINGS:
            meaning = "unknown"
        return MoneyCell(value=v, raw=raw_str, column=column, meaning=meaning)

    return None


def _parse_row(row_obj: dict) -> OmniMoneyRow | None:
    """Parse one row dict (from AI response) into an OmniMoneyRow."""
    label = str(row_obj.get("label", "")).strip()
    if not label:
        return None

    row_type = str(row_obj.get("row_type", "unknown")).strip().lower()
    if row_type not in ROW_TYPES:
        row_type = "unknown"

    evidence = str(row_obj.get("evidence", "")).strip()
    cells: list[MoneyCell] = []
    raw_amounts = row_obj.get("amounts", [])

    if raw_amounts and isinstance(raw_amounts[0], dict) and "value" in raw_amounts[0]:
        # New schema: each amount is {"value": 0.00, "raw": "...", "column": "...", "meaning": "..."}
        for cell_obj in raw_amounts:
            cell = _parse_cell(cell_obj)
            if cell is not None:
                cells.append(cell)
    else:
        # Legacy schema: amounts is a list of floats; meanings in column_meanings
        raw_meanings = row_obj.get("column_meanings", [])
        for i, v in enumerate(raw_amounts):
            fv = _to_float(v)
            if fv is None:
                continue
            meaning = "unknown"
            if i < len(raw_meanings):
                m = str(raw_meanings[i]).strip().lower()
                meaning = _LEGACY_MEANING_MAP.get(m, "unknown")
            cells.append(MoneyCell(value=fv, raw=f"${fv:,.2f}", column="amount", meaning=meaning))

    if not cells:
        return None

    return OmniMoneyRow(label=label, cells=cells, row_type=row_type, evidence=evidence)


def parse_omni_rows(raw: str) -> list[OmniMoneyRow]:
    """Parse the AI's JSON response into OmniMoneyRow objects.

    Handles both the new DocumentTables schema ({"tables": [...]}) and
    the legacy {"rows": [...]} schema for backward compatibility.
    """
    obj = _extract_json_object(raw)
    if not obj:
        _log.warning("omni_perception: failed to parse JSON from model output")
        return []

    rows: list[OmniMoneyRow] = []

    if "tables" in obj:
        # New DocumentTables schema
        for table in obj.get("tables", []):
            for row_obj in table.get("rows", []):
                if not isinstance(row_obj, dict):
                    continue
                row = _parse_row(row_obj)
                if row is not None:
                    rows.append(row)
    elif "rows" in obj:
        # Legacy flat-rows schema
        for row_obj in obj.get("rows", []):
            if not isinstance(row_obj, dict):
                continue
            row = _parse_row(row_obj)
            if row is not None:
                rows.append(row)

    _log.debug("omni_perception: parsed %d rows", len(rows))
    return rows


def parse_document_tables(raw: str) -> dict:
    """Return the full parsed DocumentTables dict (for authority name extraction etc.)."""
    return _extract_json_object(raw) or {}


# ---------------------------------------------------------------------------
# Main classifier entry point
# ---------------------------------------------------------------------------


def classify_table_with_omni(
    *,
    text: str,
    ai_client: "AIClient",
    doc_type: str = "council",
    image_data_url: str | None = None,
) -> list[OmniMoneyRow]:
    """Call the AI to classify the document's rate/charge table into typed rows.

    Parameters
    ----------
    text:
        Parsed document text (truncated to 12 000 chars internally).
    ai_client:
        Any AIClient-compatible instance.
    doc_type:
        One of "council", "water", "oc".
    image_data_url:
        Optional base64 data URL for a page image (multimodal models only).

    Returns
    -------
    List of OmniMoneyRow — empty on any failure.
    """
    prompt = _build_prompt(doc_type, text)

    try:
        result = ai_client.complete(prompt, image_data_url=image_data_url)
        raw = (result.raw_text if hasattr(result, "raw_text") else str(result)).strip()
    except Exception as exc:
        _log.warning("omni_perception: model call failed — %s", exc)
        return []

    rows = parse_omni_rows(raw)
    _log.debug("omni_perception: %d rows returned", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Deterministic calculator — zero AI, pure logic on MoneyCell data
# ---------------------------------------------------------------------------


def _best_annual_cell(row: OmniMoneyRow) -> float | None:
    """Pick the most appropriate cell value from a charge row.

    Priority:
    1. A cell whose meaning is in _ANNUAL_MEANINGS (current_charge, levied, annual_total)
    2. Single-cell row with non-ignored meaning
    3. None — unsafe to use
    """
    for cell in row.cells:
        if cell.meaning in _ANNUAL_MEANINGS:
            return cell.value
    # Single-cell row: use it if the meaning isn't explicitly wrong
    if len(row.cells) == 1 and row.cells[0].meaning not in _IGNORED_MEANINGS:
        return row.cells[0].value
    return None


def _best_total_cell(row: OmniMoneyRow) -> float | None:
    """For annual_total rows: prefer annual_total meaning, then levied, then last non-ignored."""
    for cell in row.cells:
        if cell.meaning == "annual_total":
            return cell.value
    for cell in row.cells:
        if cell.meaning == "levied":
            return cell.value
    non_ignored = [c for c in row.cells if c.meaning not in _IGNORED_MEANINGS]
    if non_ignored:
        return non_ignored[-1].value
    return row.cells[-1].value if row.cells else None


def annual_from_omni_rows(rows: list[OmniMoneyRow]) -> tuple[float, str] | None:
    """Compute the annual amount deterministically from AI-classified rows.

    Priority
    --------
    1. Explicit annual_total row — council stated it directly (highest confidence)
    2. Sum of charge rows using annual-meaning cells
    3. Reconstruction: abs(payments) + balance + abs(rebates)

    Always ignores: adjustment, interest, balance, outstanding, payment, rebate,
    valuation, certificate_fee cells when computing the annual amount.

    Returns (amount, quote) or None.
    """
    annual_totals: list[tuple[str, float, str]] = []
    charges: list[tuple[str, float]] = []
    payments: list[float] = []
    rebates: list[float] = []
    balances: list[float] = []

    for row in rows:
        if not row.cells:
            continue

        rtype = row.row_type

        if rtype == "annual_total":
            amount = _best_total_cell(row)
            if amount is not None and amount > 0:
                annual_totals.append((row.label, amount, row.evidence))

        elif rtype in _CHARGE_ROW_TYPES:
            amount = _best_annual_cell(row)
            if amount is not None and amount > 0:
                charges.append((row.label, amount))

        elif rtype in ("payment", "adjustment"):
            payments.extend(
                abs(c.value) for c in row.cells
                if c.meaning not in _IGNORED_MEANINGS - {"payment", "adjustment"}
            )

        elif rtype == "rebate":
            rebates.extend(abs(c.value) for c in row.cells)

        elif rtype == "balance":
            balances.extend(
                c.value for c in row.cells
                if c.value >= 0 and c.meaning in {"balance", "outstanding", "unknown"}
            )

    # 1. Explicit annual total — highest priority
    if annual_totals:
        lbl, amount, evidence = annual_totals[0]
        charge_sum = round(sum(a for _, a in charges), 2)
        if charge_sum > 0 and abs(amount - charge_sum) <= 1.0:
            note = f"{lbl} ${amount:,.2f} (verified by charge-row sum ${charge_sum:,.2f})"
        elif charge_sum > 0:
            note = (
                f"{lbl} ${amount:,.2f} "
                f"(charge rows sum ${charge_sum:,.2f} — council total used)"
            )
        else:
            note = f"{lbl} ${amount:,.2f}; {evidence}"
        return round(amount, 2), note

    # 2. Sum of charge rows
    charge_sum = round(sum(a for _, a in charges), 2)
    if charge_sum > 0:
        quote = "; ".join(f"{lbl} ${a:,.2f}" for lbl, a in charges[:8])
        return charge_sum, quote

    # 3. Reconstruction: payments + balance + rebates
    if payments and balances:
        reconstructed = round(sum(payments) + max(balances) + sum(rebates), 2)
        if reconstructed > 0:
            return reconstructed, (
                f"payments ${sum(payments):,.2f} + "
                f"balance ${max(balances):,.2f} + "
                f"rebates ${sum(rebates):,.2f}"
            )

    return None


# ---------------------------------------------------------------------------
# Validation — confirm AI candidate is not just a balance/outstanding value
# ---------------------------------------------------------------------------


def validate_omni_candidate(
    amount: float,
    rows: list[OmniMoneyRow],
    balance_vals: set[float],
) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means the candidate should be discarded."""
    if amount <= 0:
        return False, "non-positive amount"

    if round(amount, 2) in {round(b, 2) for b in balance_vals}:
        return False, "matches balance/outstanding value"

    has_charge = any(r.row_type in _CHARGE_ROW_TYPES for r in rows)
    has_total = any(r.row_type == "annual_total" for r in rows)
    if not has_charge and not has_total:
        return False, "no charge or annual_total evidence rows"

    return True, "ok"


# ---------------------------------------------------------------------------
# Trigger gate — decides when to invoke Omni
# ---------------------------------------------------------------------------


def should_call_omni(
    *,
    best_source: str | None,
    best_value: float | None,
    conflict: bool,
    suspicious: bool,
    from_balance: bool,
    lower_than_balance: bool,
    unknown_table_shape: bool,
    candidate_count: int,
    single_weak_candidate: bool = False,
    candidate_disagrees_with_total_due: bool = False,
) -> bool:
    """Return True when the Omni sub-agent should be invoked.

    Never fires when a strong structural source already has the answer — that
    would just waste an AI call on a document we already resolved correctly.
    """
    if best_source is None or best_value is None:
        return True

    # Strong deterministic evidence — trust it
    if best_source in STRONG_STRUCTURAL_SOURCES and not conflict and not from_balance:
        return False

    if conflict:
        return True
    if suspicious:
        return True
    if from_balance:
        return True
    if lower_than_balance:
        return True
    if unknown_table_shape:
        return True
    if single_weak_candidate:
        return True
    if candidate_disagrees_with_total_due:
        return True

    # Single weak candidate with suspiciously low value
    if (
        candidate_count == 1
        and best_value is not None
        and best_value < 300.0
        and best_source not in STRONG_STRUCTURAL_SOURCES
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# Debug logging
# ---------------------------------------------------------------------------


def write_debug_log(
    *,
    filename: str,
    doc_type: str,
    called: bool,
    reason: str,
    model: str,
    rows: list[OmniMoneyRow],
    candidate: float | None,
    accepted: bool,
    rule_candidates: list[tuple[str, float]] | None = None,
    verifier_supported: bool | None = None,
    verifier_reason: str | None = None,
) -> None:
    """Append a structured entry to rates_omni_debug.json."""
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "file": filename,
        "doc_type": doc_type,
        "called": called,
        "reason": reason,
        "model": model,
        "rule_candidates": (
            [[src, f"${val:,.2f}"] for src, val in rule_candidates]
            if rule_candidates else []
        ),
        "ai_rows": [
            {
                "label": r.label,
                "row_type": r.row_type,
                "cells": [
                    {
                        "value": c.value,
                        "raw": c.raw,
                        "column": c.column,
                        "meaning": c.meaning,
                    }
                    for c in r.cells
                ],
                "evidence": r.evidence,
            }
            for r in rows
        ],
        "ai_candidate": f"${candidate:,.2f}" if candidate is not None else None,
        "accepted": accepted,
        "verifier_supported": verifier_supported,
        "verifier_reason": verifier_reason,
    }

    existing: list[dict] = []
    if _DEBUG_LOG_PATH.exists():
        try:
            existing = json.loads(_DEBUG_LOG_PATH.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    existing.append(entry)
    try:
        _DEBUG_LOG_PATH.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        _log.warning("omni_perception: could not write debug log — %s", exc)


# ---------------------------------------------------------------------------
# Verifier pass — AI checks whether the rule winner is supported by evidence
# ---------------------------------------------------------------------------

_VERIFIER_PROMPT = """\
You are verifying a rates extraction result for an Australian conveyancing document.

Document type: {doc_type}
Rule-extracted final amount: {final_amount}
Rule extraction source: {rule_source}

Rule candidates produced (source -> amount):
{rule_candidates_text}

AI-classified rows from the document:
{rows_text}

Question: Is {final_amount} the correct annual {doc_type} amount based on the evidence?

Criteria:
- "supported" means the amount is clearly derivable from the document evidence above.
- If charge rows sum to a materially different amount -> supported=false.
- If there is an explicit annual_total row with a different value -> supported=false.
- If the amount matches only one partial charge line while others exist -> supported=false.
- Do NOT invent amounts. Only reference amounts visible in the rows above.

Return ONLY valid JSON:
{{
  "supported": true,
  "reason": "short explanation",
  "suggested_amount": {final_amount_raw},
  "suggest_review": false
}}

If supported=false, set suggested_amount to the amount you believe is correct.
If supported=true, suggested_amount must equal {final_amount_raw}.
"""


@dataclass
class VerifierResult:
    supported: bool
    reason: str
    suggested_amount: float | None
    suggest_review: bool


def verify_extraction(
    *,
    doc_type: str,
    final_amount: float,
    rule_source: str,
    rule_candidates: list[tuple[str, float]],
    rows: list[OmniMoneyRow],
    ai_client: "AIClient",
) -> VerifierResult:
    """Ask the AI to verify that the rule-extracted amount is supported by evidence.

    Only call this when Omni rows are already available and the result is suspicious.
    Never raises — returns supported=True on any failure (fail-open).
    """
    rule_candidates_text = "\n".join(
        f"  {src}: ${val:,.2f}" for src, val in rule_candidates
    ) or "  (none)"

    rows_text = "\n".join(
        f"  [{r.row_type}] {r.label}: "
        + ", ".join(f"{c.column}={c.raw}({c.meaning})" for c in r.cells)
        + f"  evidence='{r.evidence[:80]}'"
        for r in rows
    ) or "  (no rows)"

    prompt = _VERIFIER_PROMPT.format(
        doc_type=doc_type,
        final_amount=f"${final_amount:,.2f}",
        final_amount_raw=final_amount,
        rule_source=rule_source,
        rule_candidates_text=rule_candidates_text,
        rows_text=rows_text,
    )

    try:
        result = ai_client.complete(prompt)
        raw = (result.raw_text if hasattr(result, "raw_text") else str(result)).strip()
    except Exception as exc:
        _log.warning("omni_perception verifier: call failed — %s", exc)
        return VerifierResult(
            supported=True,
            reason="verifier call failed",
            suggested_amount=None,
            suggest_review=False,
        )

    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return VerifierResult(
            supported=True,
            reason="no JSON from verifier",
            suggested_amount=None,
            suggest_review=False,
        )

    try:
        obj = json.loads(raw[start: end + 1])
    except json.JSONDecodeError:
        return VerifierResult(
            supported=True,
            reason="JSON parse error",
            suggested_amount=None,
            suggest_review=False,
        )

    supported = bool(obj.get("supported", True))
    reason = str(obj.get("reason", "")).strip()[:300]
    suggest_review = bool(obj.get("suggest_review", not supported))

    suggested_amount: float | None = None
    raw_suggested = obj.get("suggested_amount")
    if raw_suggested is not None:
        try:
            suggested_amount = round(
                float(str(raw_suggested).replace("$", "").replace(",", "")), 2
            )
        except (TypeError, ValueError):
            pass

    return VerifierResult(
        supported=supported,
        reason=reason,
        suggested_amount=suggested_amount,
        suggest_review=suggest_review,
    )
