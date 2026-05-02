"""LLM-based row classifier for council rates certificates.

Architecture: deterministic parser extracts candidate rows → LLM classifies
each row's label → deterministic calculator computes the annual amount.

The LLM never guesses an amount; it only assigns a type to each row:
  charge       — a current-year levy or service charge
  rebate       — a reduction applied to charges (pension rebate, concession)
  payment      — money already paid this year
  balance      — net amount still owing (settlement figure)
  annual_total — an explicit total line for all annual charges
  ignore       — arrears, interest, legal fees, or other non-annual items

Called only when rule-based extraction produced no result or low confidence.
With no ai_client the module is a no-op.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from triconvey_agent.ai.client import AIClient

# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------

ROW_TYPES = frozenset(
    {"charge", "rebate", "payment", "balance", "annual_total", "ignore"}
)


@dataclass
class ClassifiedRow:
    label: str
    amount: float
    row_type: str  # one of ROW_TYPES


# ---------------------------------------------------------------------------
# Candidate-row extractor — pulls every (label, amount) pair from raw text
# ---------------------------------------------------------------------------

# Any line of the form "Label text   123.45" or "Label text   $123.45".
# Uses [ \t]+ (not \s+) so it never spans a newline — stacked layouts like
# "Residential Waste\n1,839.75" must NOT be matched as a single inline row.
_CANDIDATE_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 &/(),.'\-]{1,80}?)[ \t]+\$?[ \t]*(-?[\d,]+\.\d{2})[ \t]*$",
    re.MULTILINE,
)

# Amount-only lines (stacked layout) — matched separately
_AMOUNT_ONLY_RE = re.compile(r"^\s*\$?\s*(-?[\d,]+\.\d{2})\s*$", re.MULTILINE)


def _parse_float(s: str) -> float:
    return float(s.replace("$", "").replace(",", "").strip())


def extract_all_money_rows(text: str) -> list[tuple[str, float]]:
    """Return every (label, amount) pair visible in the text.

    Handles two layouts:

    Inline (label and amount on the same line)::

        General Rate   $1,558.95
        Waste Service Charge 465.00

    Stacked (labels first, then amounts in matching order)::

        Residential/Supplementary Rate
        Recycle and Waste Charge
        ESVF Levy
        1,839.75
        65.00
        355.70

    For the stacked layout, we scan each line: if it is a pure amount we add
    it to an amount buffer; if it looks like a label we add it to a label
    buffer.  Once we have collected at least as many amounts as labels we pair
    them up.  We then merge with inline rows, deduplicating by label.
    """
    rows: list[tuple[str, float]] = []
    seen: set[str] = set()

    # ── Inline rows
    for m in _CANDIDATE_LINE_RE.finditer(text):
        label = " ".join(m.group(1).split())
        key = label.lower()
        if key in seen:
            continue
        try:
            amt = _parse_float(m.group(2))
        except ValueError:
            continue
        seen.add(key)
        rows.append((label, amt))

    # ── Stacked rows — walk line by line collecting label/amount runs
    label_buf: list[str] = []
    amount_buf: list[float] = []

    def _flush_stacked() -> None:
        if not label_buf or not amount_buf:
            label_buf.clear()
            amount_buf.clear()
            return
        # The labels that correspond to the amounts are the ones immediately
        # before the amount block.  Take only the last N labels (N = number
        # of amounts) so section headers buffered earlier don't get paired.
        paired_labels = label_buf[-len(amount_buf):]
        for lbl, amt in zip(paired_labels, amount_buf):
            key = lbl.lower()
            if key not in seen:
                seen.add(key)
                rows.append((lbl, amt))
        label_buf.clear()
        amount_buf.clear()

    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            # blank line ends a stacked block
            _flush_stacked()
            continue

        am = _AMOUNT_ONLY_RE.match(raw_line)
        if am:
            try:
                amount_buf.append(_parse_float(am.group(1)))
            except ValueError:
                pass
        else:
            # If we already have amounts buffered, this new label line starts a
            # fresh block — flush what we have first.
            if amount_buf:
                _flush_stacked()
            # Only buffer lines that look like they could be charge labels
            # (at least two words, no pure-number content).
            if re.search(r"[A-Za-z]{2,}", line) and not _CANDIDATE_LINE_RE.match(raw_line):
                label_buf.append(line)

    _flush_stacked()

    return rows


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You classify financial line items from Australian council rates certificates.

For each row assign EXACTLY one type from this list:
  charge       — a current-year annual levy or service charge
                 (e.g. General Rate, Waste Service Charge, ESVF Levy,
                  Municipal Charge, Garbage Charge, Infrastructure Levy)
  rebate       — a reduction applied to charges
                 (e.g. Pension Rebate, Concession, Discount)
  payment      — money already paid this financial year
                 (e.g. Less Payments, Receipts and Adjustments,
                  Payments Received)
  balance      — net amount still owing at settlement date
                 (e.g. Total Balance Owing, Assessment Total,
                  TOTAL OUTSTANDING, Balance Due)
  annual_total — an explicit line that states the total of all annual charges
                 BEFORE deducting payments or rebates
                 (e.g. Sub Total, TOTAL CHARGES, Current Total,
                  Rates & Charges Sub Total)
  ignore       — arrears, interest, legal fees, bank charges, refunds,
                 overpayments, or any non-annual item

Rules:
- negative amounts on charge lines are still "charge" (some councils show levied as −$X)
- $0.00 amounts are still classified by label (keep their type)
- return ONLY valid JSON, no prose

Output format:
{"rows": [{"label": "...", "amount": 0.00, "type": "charge"}]}
"""


def classify_rows_with_llm(
    rows: list[tuple[str, float]],
    ai_client: "AIClient",
) -> list[ClassifiedRow] | None:
    """Call LLM to classify each (label, amount) pair.

    Returns a list of ClassifiedRow or None if the call fails.
    The LLM is asked to return strict JSON; any parse failure returns None
    so the caller can fall back to rule-based results.
    """
    if not rows:
        return None

    rows_json = json.dumps(
        [{"label": lbl, "amount": amt} for lbl, amt in rows], indent=2
    )
    prompt = (
        f"{_SYSTEM_PROMPT}\n\nRows to classify:\n{rows_json}"
    )

    try:
        result = ai_client.complete(prompt)
        raw = (result.raw_text if hasattr(result, "raw_text") else str(result)).strip()
    except Exception:
        return None

    # Parse JSON — tolerate markdown fences
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None

    classified: list[ClassifiedRow] = []
    for item in parsed.get("rows", []):
        row_type = str(item.get("type", "ignore")).lower().strip()
        if row_type not in ROW_TYPES:
            row_type = "ignore"
        try:
            amt = float(item.get("amount", 0))
        except (TypeError, ValueError):
            amt = 0.0
        classified.append(
            ClassifiedRow(
                label=str(item.get("label", "")),
                amount=amt,
                row_type=row_type,
            )
        )
    return classified or None


# ---------------------------------------------------------------------------
# Deterministic calculator — works on classified rows
# ---------------------------------------------------------------------------


def annual_from_classified_rows(
    rows: list[ClassifiedRow],
) -> tuple[float, str] | None:
    """Compute annual amount from LLM-classified rows.

    Priority:
      1. explicit annual_total row (council stated it directly)
      2. sum of charge rows
      3. abs(payments) + balance + abs(rebates)  [reconstruction]

    Validation: if both an annual_total and a charges sum exist and they agree
    within $1, use the annual_total (more authoritative).  If they disagree by
    more than $1, prefer the explicit annual_total with a note.

    Returns (amount, quote) or None.
    """
    totals = [(r.label, r.amount) for r in rows if r.row_type == "annual_total" and r.amount > 0]
    charges = [(r.label, r.amount) for r in rows if r.row_type == "charge" and r.amount > 0]
    payments = [abs(r.amount) for r in rows if r.row_type == "payment"]
    rebates = [abs(r.amount) for r in rows if r.row_type == "rebate"]
    balances = [r.amount for r in rows if r.row_type == "balance" and r.amount >= 0]

    charges_sum = round(sum(a for _, a in charges), 2)
    explicit_total = totals[0][1] if totals else None
    explicit_label = totals[0][0] if totals else None

    def _quote(pairs: list[tuple[str, float]]) -> str:
        return "; ".join(f"{lbl} ${amt:,.2f}" for lbl, amt in pairs[:6])

    # 1. Explicit annual_total — most authoritative
    if explicit_total:
        if charges_sum > 0 and abs(explicit_total - charges_sum) <= 1.0:
            note = f"{explicit_label} ${explicit_total:,.2f} (verified against charge rows)"
        elif charges_sum > 0:
            note = (
                f"{explicit_label} ${explicit_total:,.2f} "
                f"(charge rows sum ${charges_sum:,.2f} — using explicit total)"
            )
        else:
            note = f"{explicit_label} ${explicit_total:,.2f}"
        return explicit_total, note

    # 2. Sum of charge rows
    if charges_sum > 0:
        return charges_sum, _quote(charges)

    # 3. Reconstruction: payments + balance + rebates
    if payments and balances:
        reconstructed = round(sum(payments) + max(balances) + sum(rebates), 2)
        if reconstructed > 0:
            return reconstructed, f"payments {sum(payments):,.2f} + balance {max(balances):,.2f}"

    return None
