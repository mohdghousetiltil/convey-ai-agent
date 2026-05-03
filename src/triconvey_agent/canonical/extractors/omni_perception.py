"""Nemotron Omni Perception Sub-Agent — shared across council, water, and OC extractors.

Architecture
------------
PDF text (+ optional page image)
    ↓
omni_perception.classify_table()   ← this module
    ↓
Structured OmniMoneyRow list:
  { label, amounts, row_type, column_meanings, evidence }
    ↓
Deterministic calculator (annual_from_omni_rows)
    ↓
_Candidate added as source="omni_structured_rows"
    ↓
Existing consensus resolver -> final amount / needs_review

Design rule
-----------
The model returns evidence rows only — it never writes the final amount.
All arithmetic is done deterministically in annual_from_omni_rows().

Document-type prompts
---------------------
Three prompts are provided: one for council rates, one for water authority
statements, and one for owners corporation documents.  The caller selects
the prompt via doc_type= parameter.

Trigger gate
------------
should_call_omni() returns True only when rule-based extraction is weak,
conflicting, suspicious, or produced a suspiciously low amount.  Never
called when strong structural evidence already exists.

Debug logging
-------------
Every call writes a structured entry to rates_omni_debug.json in the
process working directory.  This file accumulates entries across
documents — clear it between runs if needed.
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

ROW_TYPES = frozenset(
    {"charge", "rebate", "payment", "balance", "annual_total", "ignore"}
)

COLUMN_MEANINGS = frozenset(
    {
        "levied", "current", "charge", "annual",
        "rebate", "payment",
        "balance", "outstanding", "due",
        "total",
        "unknown",
    }
)

# Sources considered "strong structural" — Omni should NOT override these.
STRONG_STRUCTURAL_SOURCES = frozenset(
    {
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
        "date_levied_verified_by_paid_due",
        "zero_adjustments_total_due",
        # water-specific strong sources (gippsland_x3 excluded — can be partial)
        "explicit_annual",
        "gww_annual_column",
        # OC-specific strong sources
        "cert_fee_block",
    }
)

_DEBUG_LOG_PATH = Path.cwd() / "rates_omni_debug.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class OmniMoneyRow:
    label: str
    amounts: list[float]
    row_type: str                    # one of ROW_TYPES
    column_meanings: list[str]       # parallel to amounts, one of COLUMN_MEANINGS each
    evidence: str


# ---------------------------------------------------------------------------
# Document-type prompts
# ---------------------------------------------------------------------------

_COUNCIL_PROMPT = """\
You are reading an Australian council Land Information Certificate or Rates Notice.

Your task is NOT to calculate the final answer.
Your task is to extract the rates/charges table into structured rows.

Return ONLY valid JSON with this exact shape:
{
  "rows": [
    {
      "label": "string",
      "amounts": [0.00],
      "row_type": "charge|rebate|payment|balance|annual_total|ignore",
      "column_meanings": ["levied|current|charge|annual|rebate|payment|balance|outstanding|due|total|unknown"],
      "evidence": "short quote copied from document"
    }
  ],
  "notes": "short explanation of table structure"
}

Rules:
- Do not invent amounts. Use only amounts visible in the supplied text or image.
- Preserve exact dollar amounts (no rounding).
- Current-year levies, rates, waste charges, garbage charges, municipal charges, ESVF,
  fire services levy -> row_type "charge".
- Payments, receipts, credits -> row_type "payment".
- Pension rebates, concessions, discounts -> row_type "rebate".
- Balance outstanding, total due, amount due, owing, payable -> row_type "balance"
  UNLESS the table clearly says this is the total current-year levied amount.
- If a row says: Current Rates Levied, Total Charges, Rates & Charges Sub Total,
  Current Years Rates and Charges Sub Total, Sub Total -> row_type "annual_total".
- In tables with columns like Levied / Balance, the first column is the annual/current
  charge; the balance/outstanding column is the settlement balance.
- In matrix tables with a Total column on a Current Rates Levied row, classify the
  row as annual_total and include the total amount.
- $0.00 amounts are still classified by label — do not omit them.
- If unsure, use row_type "ignore" or column_meanings "unknown".
"""

_WATER_PROMPT = """\
You are reading an Australian water authority Information Statement or rates notice.

Your task is to extract the service fee and usage charge table into structured rows.

Return ONLY valid JSON with this exact shape:
{
  "rows": [
    {
      "label": "string",
      "amounts": [0.00],
      "row_type": "charge|rebate|payment|balance|annual_total|ignore",
      "column_meanings": ["levied|current|charge|annual|rebate|payment|balance|outstanding|due|total|unknown"],
      "evidence": "short quote copied from document"
    }
  ],
  "notes": "short explanation of table structure, period type (daily/quarterly/annual)"
}

Rules:
- Do not invent amounts. Use only amounts visible in the supplied text or image.
- Preserve exact dollar amounts.
- Water service fees, sewerage service fees, drainage fees, environmental levies,
  bulk water charges -> row_type "charge".
- Usage charges (kL usage at $/kL or ¢/kL) -> row_type "charge".
- Payments, credits -> row_type "payment".
- Concessions, rebates -> row_type "rebate".
- Total current charges, total amount due, balance due -> row_type "balance" or
  "annual_total" if this is the full-year figure.
- If daily rates are shown (e.g. "From DATE To DATE = N days @ X¢ per day"),
  include the daily rate in the evidence field.
- Do not annualise — return the amounts as shown. The caller will annualise.
- If unsure, use row_type "ignore" or column_meanings "unknown".
"""

_OC_PROMPT = """\
You are reading an Australian Owners Corporation (OC) search report or certificate.

Your task is to extract the lot levy / fee table into structured rows.

Return ONLY valid JSON with this exact shape:
{
  "rows": [
    {
      "label": "string",
      "amounts": [0.00],
      "row_type": "charge|rebate|payment|balance|annual_total|ignore",
      "column_meanings": ["levied|current|charge|annual|rebate|payment|balance|outstanding|due|total|unknown"],
      "evidence": "short quote copied from document"
    }
  ],
  "notes": "short explanation — annual/quarterly/monthly, which fund types found"
}

Rules:
- Do not invent amounts. Use only amounts visible in the supplied text or image.
- Preserve exact dollar amounts.
- Administration fund levy, maintenance fund levy, sinking fund levy, admin levy,
  insurance levy -> row_type "charge".
- Annual fee for the lot, current annual fees -> row_type "annual_total".
- Payments, credits -> row_type "payment".
- Rebates, concessions -> row_type "rebate".
- Balance outstanding, total owing, arrears -> row_type "balance".
- Quarterly or monthly amounts should be identified in evidence — do not annualise.
  The caller will annualise based on column_meanings.
- If unsure, use row_type "ignore" or column_meanings "unknown".
"""

_PROMPTS = {
    "council": _COUNCIL_PROMPT,
    "water": _WATER_PROMPT,
    "oc": _OC_PROMPT,
}


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------


def _extract_json_object(raw: object) -> dict | None:
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    # Strip markdown fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


def _to_float(value: object) -> float | None:
    try:
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
        return round(float(value), 2)  # type: ignore[arg-type]
    except Exception:
        return None


def parse_omni_rows(raw: object) -> list[OmniMoneyRow]:
    """Parse the model's JSON output into a list of OmniMoneyRow."""
    obj = _extract_json_object(raw)
    if not obj:
        _log.warning("omni_perception: failed to parse JSON from empty/non-JSON model output")
        return []

    rows: list[OmniMoneyRow] = []
    for item in obj.get("rows", []):
        label = str(item.get("label", "")).strip()
        if not label:
            continue

        row_type = str(item.get("row_type", "ignore")).strip().lower()
        if row_type not in ROW_TYPES:
            row_type = "ignore"

        amounts: list[float] = []
        for v in item.get("amounts", []):
            parsed = _to_float(v)
            if parsed is not None:
                amounts.append(parsed)

        if not amounts:
            continue

        raw_meanings = item.get("column_meanings", [])
        column_meanings: list[str] = []
        for v in raw_meanings:
            m = str(v).strip().lower()
            column_meanings.append(m if m in COLUMN_MEANINGS else "unknown")

        # Pad / trim to match amounts length
        while len(column_meanings) < len(amounts):
            column_meanings.append("unknown")
        column_meanings = column_meanings[: len(amounts)]

        evidence = str(item.get("evidence", "")).strip()

        rows.append(
            OmniMoneyRow(
                label=label,
                amounts=amounts,
                row_type=row_type,
                column_meanings=column_meanings,
                evidence=evidence,
            )
        )

    return rows


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
    """Call the Omni model to classify the rates/charges table.

    Parameters
    ----------
    text:
        Parsed document text (truncated to 12 000 chars internally).
    ai_client:
        Any AIClient-compatible instance (OpenRouterClient, fake, etc.).
    doc_type:
        One of "council", "water", "oc".
    image_data_url:
        Optional base64 data URL for a page image (multimodal models only).

    Returns
    -------
    List of OmniMoneyRow — empty on any failure.
    """
    base_prompt = _PROMPTS.get(doc_type, _COUNCIL_PROMPT)
    prompt = base_prompt + "\n\nDOCUMENT TEXT:\n" + text[:12_000]

    try:
        result = ai_client.complete(prompt, image_data_url=image_data_url)
        raw = getattr(result, "raw_text", None)
    except Exception as exc:
        _log.warning("omni_perception: model call failed — %s", exc)
        return []

    if raw is None:
        _log.warning("omni_perception: model call returned no raw_text")
        return []

    rows = parse_omni_rows(raw)
    _log.debug("omni_perception: parsed %d rows from model output", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Deterministic calculator — works on OmniMoneyRow lists
# ---------------------------------------------------------------------------

_PREFERRED_ANNUAL_MEANINGS = frozenset(
    {"levied", "current", "charge", "annual", "total"}
)


def _choose_annual_amount(row: OmniMoneyRow) -> float | None:
    """Pick the best amount from a multi-column row for annual purposes."""
    # Prefer an amount whose column is explicitly annual/levied/charge/total
    for amount, meaning in zip(row.amounts, row.column_meanings):
        if meaning in _PREFERRED_ANNUAL_MEANINGS:
            return amount
    # Single-amount rows: safe to use directly
    if len(row.amounts) == 1:
        return row.amounts[0]
    # Multiple amounts, no clear column — unsafe to guess
    return None


def _choose_total_amount(row: OmniMoneyRow) -> float | None:
    """For annual_total rows: prefer 'total' column, else last amount."""
    for amount, meaning in zip(row.amounts, row.column_meanings):
        if meaning == "total":
            return amount
    return row.amounts[-1] if row.amounts else None


def annual_from_omni_rows(rows: list[OmniMoneyRow]) -> tuple[float, str] | None:
    """Compute the annual amount from Omni-classified rows.

    Priority
    --------
    1. explicit annual_total row — council stated it directly
    2. sum of charge rows with known column meanings
    3. reconstruction: abs(payments) + balance + abs(rebates)

    Returns (amount, quote) or None.
    """
    annual_totals: list[tuple[str, float, str]] = []
    charges: list[tuple[str, float, str]] = []
    payments: list[float] = []
    rebates: list[float] = []
    balances: list[float] = []

    for row in rows:
        if not row.amounts:
            continue

        if row.row_type == "annual_total":
            amount = _choose_total_amount(row)
            if amount is not None and amount > 0:
                annual_totals.append((row.label, amount, row.evidence))

        elif row.row_type == "charge":
            amount = _choose_annual_amount(row)
            if amount is not None and amount > 0:
                charges.append((row.label, amount, row.evidence))

        elif row.row_type == "payment":
            payments.extend(abs(x) for x in row.amounts)

        elif row.row_type == "rebate":
            rebates.extend(abs(x) for x in row.amounts)

        elif row.row_type == "balance":
            balances.extend(x for x in row.amounts if x >= 0)

    # 1. Explicit annual total
    if annual_totals:
        lbl, amount, evidence = annual_totals[0]
        charge_sum = round(sum(a for _, a, _ in charges), 2)
        if charge_sum > 0 and abs(amount - charge_sum) <= 1.0:
            note = f"{lbl} ${amount:,.2f} (verified by charge-row sum)"
        elif charge_sum > 0:
            note = (
                f"{lbl} ${amount:,.2f} "
                f"(charge rows sum ${charge_sum:,.2f} — using explicit total)"
            )
        else:
            note = f"{lbl} ${amount:,.2f}; {evidence}"
        return round(amount, 2), note

    # 2. Sum of charge rows
    charge_sum = round(sum(a for _, a, _ in charges), 2)
    if charge_sum > 0:
        quote = "; ".join(f"{lbl} ${a:,.2f}" for lbl, a, _ in charges[:8])
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
# Validation
# ---------------------------------------------------------------------------


def validate_omni_candidate(
    amount: float,
    rows: list[OmniMoneyRow],
    balance_vals: set[float],
) -> tuple[bool, str]:
    """Return (ok, reason).  ok=False means the candidate should be discarded."""
    if amount <= 0:
        return False, "non-positive amount"

    if round(amount, 2) in {round(b, 2) for b in balance_vals}:
        return False, "matches balance/outstanding value"

    charge_rows = [r for r in rows if r.row_type == "charge"]
    if not charge_rows and not any(r.row_type == "annual_total" for r in rows):
        return False, "no charge or annual_total evidence rows"

    return True, "ok"


# ---------------------------------------------------------------------------
# Trigger gate
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
    # Additional trigger signals
    single_weak_candidate: bool = False,
    candidate_disagrees_with_total_due: bool = False,
) -> bool:
    """Return True when the Omni sub-agent should be invoked.

    Never fires when a strong structural source already has the answer.

    New signals
    -----------
    single_weak_candidate:
        True when only one candidate was found and it came from a weak source
        (e.g. date_levied_scan with one matched row, inline_charge_rows with
        a single low-value result).  This is the Alpine-style failure mode.
    candidate_disagrees_with_total_due:
        True when the best rule candidate is significantly lower than a
        "Total Due" or "Total payable" value found in the document.
    """
    if best_source is None or best_value is None:
        return True

    # Strong deterministic evidence — trust it, skip the model call
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

    # Only one weak candidate and the amount looks too low
    if (
        candidate_count == 1
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
                "amounts": r.amounts,
                "row_type": r.row_type,
                "column_meanings": r.column_meanings,
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
# Verifier pass
# ---------------------------------------------------------------------------

_VERIFIER_PROMPT = """\
You are verifying a rates extraction result for an Australian conveyancing document.

Document type: {doc_type}
Rule-extracted final amount: {final_amount}
Rule extraction source: {rule_source}

Rule candidates that were produced:
{rule_candidates_text}

Document evidence rows (extracted from the document):
{rows_text}

Is the final extracted amount supported as the correct annual {doc_type} amount?

Rules:
- "supported" means the amount is clearly and directly derivable from the document evidence.
- If charge rows sum to a different amount than the final, "supported" is false.
- If there is a Total Due / Sub Total in the document that is significantly different, "supported" is false.
- If the final amount matches only one of several charge rows while others were missed, "supported" is false.
- Do not invent new amounts. Only reference amounts visible in the evidence rows above.

Return ONLY valid JSON:
{{
  "supported": false,
  "reason": "short explanation of why the amount is or is not supported",
  "suggested_amount": 900.97,
  "suggest_review": true
}}

If supported is true, "suggested_amount" should equal the final amount.
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
    """Ask the AI to verify that the final extracted amount is supported by document evidence.

    Only call this when Omni has already been invoked (rows are available) and
    the result is suspicious — e.g. a single weak candidate that is much lower
    than a balance / Total Due in the document.

    Returns a VerifierResult with supported=True/False + suggested_amount.
    Never raises — returns supported=True on any failure (fail open).
    """
    rule_candidates_text = "\n".join(
        f"  - {src}: ${val:,.2f}" for src, val in rule_candidates
    ) or "  (none)"

    rows_text = "\n".join(
        f"  [{r.row_type}] {r.label}: {r.amounts}  cols={r.column_meanings}  evidence='{r.evidence[:80]}'"
        for r in rows
    ) or "  (no rows)"

    prompt = _VERIFIER_PROMPT.format(
        doc_type=doc_type,
        final_amount=f"${final_amount:,.2f}",
        rule_source=rule_source,
        rule_candidates_text=rule_candidates_text,
        rows_text=rows_text,
    )

    try:
        result = ai_client.complete(prompt)
        raw = (result.raw_text if hasattr(result, "raw_text") else str(result)).strip()
    except Exception as exc:
        _log.warning("omni_perception verifier: AI call failed — %s", exc)
        return VerifierResult(supported=True, reason="verifier call failed", suggested_amount=None, suggest_review=False)

    # Parse JSON
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return VerifierResult(supported=True, reason="no JSON from verifier", suggested_amount=None, suggest_review=False)

    try:
        obj = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return VerifierResult(supported=True, reason="verifier JSON parse error", suggested_amount=None, suggest_review=False)

    supported = bool(obj.get("supported", True))
    reason = str(obj.get("reason", "")).strip()[:300]
    suggest_review = bool(obj.get("suggest_review", not supported))

    suggested_amount: float | None = None
    raw_suggested = obj.get("suggested_amount")
    if raw_suggested is not None:
        try:
            suggested_amount = round(float(str(raw_suggested).replace("$", "").replace(",", "")), 2)
        except (TypeError, ValueError):
            pass

    return VerifierResult(
        supported=supported,
        reason=reason,
        suggested_amount=suggested_amount,
        suggest_review=suggest_review,
    )
