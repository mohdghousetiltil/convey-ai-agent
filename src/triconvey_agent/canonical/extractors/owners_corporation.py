"""Owners corporation document extractor -- evidence-scoring candidate engine.

Pipeline
--------
1. Split document into semantic blocks (page + paragraph boundaries).
2. Classify each block: CERTIFICATE_FEE | LEVY_SCHEDULE | INVOICE |
   LEDGER | INSURANCE | MINUTES | NOISE.
3. Extract every dollar amount from each block with its frequency context.
4. Build OcCandidate objects: source_amount + frequency -> annual_amount.
5. Score candidates by (block_type_rank x frequency_confidence).
6. Winner = highest-scoring candidate above minimum threshold.
7. Emit Fact with explainable notes + ignored_amounts log.

Key design rule: NEVER pick the largest number. Pick the most contextually
supported annual-fee-for-the-lot amount.
"""
from __future__ import annotations

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

EXTRACTOR_NAME = "rule:owners_corporation_v1"

# ---------------------------------------------------------------------------
# Document trigger -- must match for extractor to activate
# ---------------------------------------------------------------------------

_DOC_TRIGGER_RE = re.compile(
    r"(?:"
    r"owners?\s+corporation\s+(?:search\s+)?report"
    r"|owners?\s+corporation\s+(?:certificate|basic\s+report|information)"
    r"|owners?\s+corporation\s+levy\s+notice"
    r"|owners?\s+corporation"
    r"|oc\s+(?:certificate|report|fee\s+schedule|levy\s+notice|search)"
    r"|strata\s+(?:certificate|fees?\s+schedule|levy\s+notice)"
    r"|body\s+corporate\s+(?:certificate|search\s+report|levy)"
    r")",
    re.IGNORECASE,
)

# Documents that are definitively NOT owners corporation documents — if any of
# these phrases appear and "owners corporation" / "body corporate" / "strata"
# do NOT appear, bail out immediately.
_NON_OC_EXCLUSION_RE = re.compile(
    r"(?:"
    r"land\s+information\s+certificate"
    r"|water\s+information\s+statement"
    r"|section\s+158\s+of\s+the\s+water\s+act"
    r"|land\s+tax\s+certificate"
    r"|planning\s+certificate"
    r"|municipal\s+rates?"
    r"|council\s+rates?\s+(?:notice|assessment)"
    r")",
    re.IGNORECASE,
)

_OC_CORE_RE = re.compile(
    r"(?:owners?\s+corporation|body\s+corporate|strata)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Block classification -- scoring keywords
# ---------------------------------------------------------------------------

# Positive signals: this block is about current fees for this lot
_CERT_FEE_POSITIVE: list[tuple[str, int]] = [
    ("current annual fees", 6),
    ("current fees", 5),
    ("annual fees for the lot", 6),
    ("fees for the lot", 5),
    ("total annual", 5),
    ("annual levy", 4),
    ("annual fee", 4),
    ("per annum", 4),
    ("per year", 3),
    ("payable annually", 5),
    ("payable quarterly", 4),
    ("payable monthly", 3),
    ("quarterly instalments", 4),
    ("per quarter", 3),
    ("administration fund", 2),
    ("maintenance fund", 2),
    ("sinking fund levy", 3),
    ("admin fund levy", 3),
    ("levy notice", 3),
    ("lot entitlement", 2),
]

# Negative signals: this block is about noise (insurance, ledger, balance sheet, etc.)
_CERT_FEE_NEGATIVE: list[tuple[str, int]] = [
    ("insurance cover", 5),
    ("certificate of currency", 5),
    ("sum insured", 5),
    ("public liability", 4),
    ("premium", 4),
    ("building cover", 4),
    ("balance sheet", 5),
    ("owner ledger", 5),
    ("owners ledger", 5),
    ("amount prepaid", 4),
    ("fee outstanding", 4),
    ("amount outstanding", 3),
    ("arrears", 3),
    ("special levy", 4),
    ("cash at bank", 4),
    ("total funds held", 4),
    ("settlement adjustment", 4),
    ("admin fee", 2),          # admin fee != admin fund levy
    ("compliance fee", 3),
    ("opening balance", 3),
    ("closing balance", 3),
    ("receipt", 2),
    ("payment made", 2),
]

# Block type ranks for candidate selection (higher = more trusted)
_BLOCK_TYPE_RANK: dict[str, int] = {
    "certificate_fee":  100,
    "levy_schedule":     80,
    "invoice":           55,
    "minutes":           30,
    "ledger":            20,
    "insurance":         10,
    "noise":              0,
}

# ---------------------------------------------------------------------------
# Dollar amount pattern
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"\$([\d,]+\.\d{2})")

# ---------------------------------------------------------------------------
# Frequency classification -- (regex, multiplier, label, base_confidence)
# ---------------------------------------------------------------------------

_FREQUENCY_RULES: list[tuple[re.Pattern, int, str, float]] = [
    # Annual / per annum (multiplier = 1)
    (re.compile(
        r"(?:annual(?:ly)?|per\s+annum|per\s+year|for\s+the\s+(?:financial\s+)?year"
        r"|payable\s+annually|total\s+annual)",
        re.IGNORECASE,
    ), 1, "annual", 0.97),
    # Quarterly / per quarter (multiplier = 4)
    (re.compile(
        r"(?:quarter(?:ly)?|per\s+quarter|quarterly\s+instalment"
        r"|payable\s+quarterly|per\s+quarter)",
        re.IGNORECASE,
    ), 4, "quarterly", 0.93),
    # Monthly (multiplier = 12)
    (re.compile(
        r"(?:monthly|per\s+month|payable\s+monthly)",
        re.IGNORECASE,
    ), 12, "monthly", 0.88),
    # Half-yearly / semi-annual (multiplier = 2)
    (re.compile(
        r"(?:half[- ]?year(?:ly)?|semi[- ]?annual|per\s+half[- ]?year"
        r"|payable\s+half[- ]?yearly)",
        re.IGNORECASE,
    ), 2, "half_yearly", 0.90),
]

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Budget-block exclusion -- whole-OC budget figures must never be used as
# per-lot fees unless the same block ALSO contains a per-lot override phrase.
# ---------------------------------------------------------------------------

# Terms that flag a block as a whole-OC budget (not per-lot fees)
_BUDGET_BLOCK_TERMS: list[str] = [
    "proposed budget",
    "budget",
    "levies due",
    "total revenue",
    "total expenses",
    "management fees",
    "insurance--premiums",
    "annual management fee",
    "total administration",
    "total maintenance",
    "committee report",
    "annual general meeting",
    "agm",
]

# Override phrases: if a budget block ALSO contains one of these, it is still
# eligible (e.g. an OC cert that happens to summarise the budget alongside fees)
_CERT_FEE_LOT_PHRASES: list[str] = [
    "current annual fees for the lot",
    "current fees for the lot",
    "fees for the above lot",
    "fees for the lot",
]

# Fast-path pattern: "current annual fees for the lot total $X"
# If found anywhere in the document, lock onto $X immediately.
_CERT_FEE_FASTPATH_RE = re.compile(
    r"current\s+annual\s+fees?\s+for\s+the\s+lot\s+total\s+\$([\d,]+\.\d{2})",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Bad-amount labels -- amounts near these words should be excluded
# ---------------------------------------------------------------------------

_BAD_AMOUNT_RE = re.compile(
    r"(?:"
    r"fee\s+outstanding|amount\s+outstanding|arrears"
    r"|amount\s+prepaid|prepaid"
    r"|total\s+funds\s+held|cash\s+at\s+bank|fund\s+balance"
    r"|insurance\s+premium|building\s+premium|premium"
    r"|sum\s+insured|public\s+liability|building\s+cover"
    r"|special\s+levy"
    r"|settlement\s+adjustment"
    r"|opening\s+balance|closing\s+balance"
    r"|receipt|payment\s+made"
    r"|compliance\s+fee"
    r"|owner[s]?\s+ledger"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Candidate dataclass
# ---------------------------------------------------------------------------


@dataclass
class OcCandidate:
    """A candidate annual OC fee amount."""
    source_amount: float          # dollar amount as found in text
    annual_amount: float          # after multiplier applied
    frequency: str                # "annual" | "quarterly" | "monthly" | "half_yearly" | "unknown"
    multiplier: int               # 1 | 4 | 12 | 2
    block_score: int              # net score from block classification
    block_type: str               # "certificate_fee" | "levy_schedule" | ...
    freq_confidence: float        # 0-1 from frequency rule
    evidence: str                 # verbatim text snippet
    page: int                     # 1-based page
    ignored: bool = False         # True if excluded by bad-amount filter
    ignore_reason: str = ""

    @property
    def annual_str(self) -> str:
        return f"${self.annual_amount:,.2f}"

    @property
    def overall_score(self) -> float:
        if self.ignored:
            return -999.0
        rank = _BLOCK_TYPE_RANK.get(self.block_type, 0)
        return rank * self.freq_confidence + self.block_score * 0.5


# ---------------------------------------------------------------------------
# Block splitting
# ---------------------------------------------------------------------------


@dataclass
class TextBlock:
    text: str
    page: int
    block_type: str = "noise"
    score: int = 0


def _split_blocks(text: str, pages: list[str] | None = None) -> list[TextBlock]:
    """Split document text into scored semantic blocks.

    Each paragraph (blank-line-separated chunk) becomes a block. If page
    texts are available, page number is tracked. Block type is classified
    by net keyword score.
    """
    blocks: list[TextBlock] = []

    if pages:
        for page_idx, page_text in enumerate(pages, start=1):
            for para in re.split(r"\n{2,}", page_text):
                para = para.strip()
                if len(para) < 10:
                    continue
                blocks.append(_classify_block(para, page=page_idx))
    else:
        page = 1
        for para in re.split(r"\n{2,}", text):
            para = para.strip()
            if not para:
                continue
            # Simple page-break heuristic: look for form-feed or "Page N"
            if re.search(r"\f|^\s*page\s+\d+\s*$", para, re.IGNORECASE):
                page += 1
                continue
            if len(para) < 10:
                continue
            blocks.append(_classify_block(para, page=page))

    return blocks


def _is_budget_block(lower: str) -> bool:
    """Return True if this block looks like a whole-OC budget section."""
    has_budget_term = any(term in lower for term in _BUDGET_BLOCK_TERMS)
    if not has_budget_term:
        return False
    # Exempt if the block also contains a per-lot fee override phrase
    has_lot_phrase = any(phrase in lower for phrase in _CERT_FEE_LOT_PHRASES)
    return not has_lot_phrase


def _classify_block(text: str, *, page: int) -> TextBlock:
    """Score a paragraph and assign its block_type."""
    lower = text.lower()
    score = 0
    for phrase, weight in _CERT_FEE_POSITIVE:
        if phrase in lower:
            score += weight
    for phrase, weight in _CERT_FEE_NEGATIVE:
        if phrase in lower:
            score -= weight

    # Hard-demote budget blocks -- they contain whole-OC figures, not per-lot
    if _is_budget_block(lower):
        return TextBlock(text=text, page=page, block_type="noise", score=-50)

    if score >= 8:
        block_type = "certificate_fee"
    elif score >= 4:
        block_type = "levy_schedule"
    elif score >= 1:
        block_type = "invoice"
    elif score <= -4:
        block_type = "insurance" if any(
            w in lower for w in ("insurance", "premium", "sum insured", "public liability")
        ) else "ledger" if any(
            w in lower for w in ("ledger", "balance", "receipt", "payment")
        ) else "noise"
    else:
        block_type = "noise"

    return TextBlock(text=text, page=page, block_type=block_type, score=score)


# ---------------------------------------------------------------------------
# Candidate extraction from a single block
# ---------------------------------------------------------------------------


def _extract_candidates_from_block(block: TextBlock) -> list[OcCandidate]:
    """Find every dollar amount in a block and create an OcCandidate for each."""
    candidates: list[OcCandidate] = []
    text = block.text

    for amount_m in _AMOUNT_RE.finditer(text):
        raw = amount_m.group(1)
        try:
            source_amount = float(raw.replace(",", ""))
        except ValueError:
            continue
        if source_amount <= 0:
            continue

        # Window of text around the amount for frequency detection
        start = max(0, amount_m.start() - 150)
        end = min(len(text), amount_m.end() + 150)
        window = text[start:end]

        # Check bad-amount labels near the amount
        ignored = False
        ignore_reason = ""
        bad_m = _BAD_AMOUNT_RE.search(window)
        if bad_m:
            ignored = True
            ignore_reason = bad_m.group(0).strip()

        # Detect frequency
        frequency = "unknown"
        multiplier = 1
        freq_confidence = 0.70  # default if no frequency word found
        for freq_re, mult, freq_label, conf in _FREQUENCY_RULES:
            if freq_re.search(window):
                frequency = freq_label
                multiplier = mult
                freq_confidence = conf
                break

        annual_amount = round(source_amount * multiplier, 2)

        # Compact evidence snippet
        evidence = " ".join(window.split())[:220]

        candidates.append(OcCandidate(
            source_amount=source_amount,
            annual_amount=annual_amount,
            frequency=frequency,
            multiplier=multiplier,
            block_score=block.score,
            block_type=block.block_type,
            freq_confidence=freq_confidence,
            evidence=evidence,
            page=block.page,
            ignored=ignored,
            ignore_reason=ignore_reason,
        ))

    return candidates


# ---------------------------------------------------------------------------
# OC name extraction (unchanged logic)
# ---------------------------------------------------------------------------

_OC_NUMBERED_RE = re.compile(
    r"\bOwners?\s+Corporation\s+(?:No\.?\s*)?(\d+)\b",
    re.IGNORECASE,
)
_OC_NAMED_RE = re.compile(
    r"\b(?!Department|State|Victorian|Commonwealth|Federal|Government|Municipal|Local"
    r"|See|Refer|An\s|The\s|This\s|Any\s|Each\s|No\s|Each\b)"
    r"([A-Z][A-Za-z]+(?:\s+[A-Za-z]+){0,4})\s+Owners?\s+Corporation\b",
)
_OC_NAMED_EXCLUDED = frozenset({
    "see", "refer", "the", "an", "a", "this", "that", "any", "each",
    "no", "such", "above", "below", "said", "relevant", "applicable",
})
_PLAN_NUMBER_RE = re.compile(
    r"\b((?:PS|LP|CP|RP|SP|STA|REG)\s*\d{4,7}[A-Z]?)\b",
    re.IGNORECASE,
)


def _extract_oc_name(doc: Document, text: str) -> list[Fact]:
    m_named = _OC_NAMED_RE.search(text)
    if m_named:
        prefix_word = m_named.group(1).split()[0].lower()
        if prefix_word not in _OC_NAMED_EXCLUDED:
            name = " ".join(m_named.group(0).split())
            return [_make_fact(doc, P.RATES_OC_AUTHORITY_NAME, name, name,
                               confidence=0.90, notes="Named OC body from document text")]

    m_num = _OC_NUMBERED_RE.search(text)
    haystack = f"{doc.filename or ''}\n{text}"
    m_plan = _PLAN_NUMBER_RE.search(haystack)
    plan_ref = m_plan.group(1).upper().replace(" ", "") if m_plan else None

    if m_num and plan_ref:
        name = f"Owners Corporation {m_num.group(1)} (Plan {plan_ref})"
        return [_make_fact(doc, P.RATES_OC_AUTHORITY_NAME, name, m_num.group(0),
                           confidence=0.92, notes=f"OC number + plan reference {plan_ref}")]
    if m_num:
        name = f"Owners Corporation {m_num.group(1)}"
        return [_make_fact(doc, P.RATES_OC_AUTHORITY_NAME, name, m_num.group(0),
                           confidence=0.85, notes="OC number from document")]
    if plan_ref:
        name = f"Owners Corporation (Plan {plan_ref})"
        return [_make_fact(doc, P.RATES_OC_AUTHORITY_NAME, name, f"Plan {plan_ref}",
                           confidence=0.80, notes=f"OC inferred from plan ref {plan_ref}")]

    return [_make_fact(doc, P.RATES_OC_AUTHORITY_NAME, "Owners Corporation",
                       "Owners Corporation document detected",
                       confidence=0.75, notes="Generic fallback -- no specific OC name found")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fact(
    doc: Document,
    path: str,
    value: object,
    quote: str,
    *,
    confidence: float,
    notes: str | None = None,
) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[Source(file=doc.filename, page=None, quote=quote[:300], quote_verified=True)],
        extractor=EXTRACTOR_NAME,
        notes=notes,
    )


def _oc_debug_log(
    doc: Document,
    blocks: list[TextBlock],
    all_candidates: list[OcCandidate],
    winner: OcCandidate | None,
) -> None:
    fname = doc.filename or "unknown"
    print(f"\n  +- OC CANDIDATE ENGINE [{fname}]")

    block_summary: dict[str, int] = {}
    for b in blocks:
        block_summary[b.block_type] = block_summary.get(b.block_type, 0) + 1
    print(f"  |  Blocks  : {dict(sorted(block_summary.items()))}")

    eligible = [c for c in all_candidates if not c.ignored]
    ignored  = [c for c in all_candidates if c.ignored]
    print(f"  |  Candidates: {len(eligible)} eligible, {len(ignored)} ignored")

    if winner:
        mult_note = f" x {winner.multiplier}" if winner.multiplier > 1 else ""
        print(f"  |  WINNER  : {winner.annual_str}  "
              f"(source ${winner.source_amount:,.2f}{mult_note}, "
              f"{winner.frequency}, block={winner.block_type}, "
              f"score={winner.overall_score:.1f})")
        print(f"  |  Evidence: {winner.evidence[:120]}")
    else:
        print(f"  |  WINNER  : (none)")

    if eligible:
        print(f"  |  All eligible (top 5 by score):")
        for c in sorted(eligible, key=lambda x: -x.overall_score)[:5]:
            mark = "[W]" if c is winner else "   "
            print(f"  |    {mark} ${c.annual_amount:>10,.2f}  "
                  f"{c.frequency:<12} block={c.block_type:<18} "
                  f"score={c.overall_score:5.1f}  p{c.page}")

    if ignored:
        print(f"  |  Ignored amounts:")
        for c in ignored[:6]:
            print(f"  |      ${c.source_amount:>10,.2f}  reason='{c.ignore_reason}'  p{c.page}")

    print(f"  +---------------------------------------------")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_owners_corporation_facts(doc: Document, *, ai_client=None) -> list[Fact]:
    text = doc.raw_text or doc.normalized_text or ""
    file_name = doc.filename or ""
    haystack = f"{file_name}\n{text}"
    if not _DOC_TRIGGER_RE.search(haystack):
        return []

    # Hard-exclude documents that are definitively not OC documents (council land
    # certificates, water statements, land tax, planning certs).  The trigger above
    # can still fire because Victorian council land certificates mention things like
    # "owners corporation" in a legal-context sentence — the exclusion ensures we
    # only proceed when the document is *about* an owners corporation.
    if _NON_OC_EXCLUSION_RE.search(haystack) and not _OC_CORE_RE.search(haystack[:500]):
        return []

    facts: list[Fact] = [
        _make_fact(
            doc, P.RATES_OWNERS_CORPORATION, True,
            "Owners Corporation document detected",
            confidence=0.92,
            notes="OC certificate/basic report implies an active owners corporation",
        ),
    ]
    facts.extend(_extract_oc_name(doc, text))

    # --- Fast path: exact certificate fee phrase on this lot ---
    # "current annual fees for the lot total $X" -> return immediately
    fp_match = _CERT_FEE_FASTPATH_RE.search(text)
    if fp_match:
        try:
            fp_amount = float(fp_match.group(1).replace(",", ""))
            fp_str = f"${fp_amount:,.2f}"
            fp_evidence = text[max(0, fp_match.start() - 60):fp_match.end() + 120].strip()
            fp_evidence = " ".join(fp_evidence.split())[:300]
            facts.append(
                _make_fact(
                    doc, P.RATES_OC_ANNUAL_AMOUNT, fp_str,
                    fp_evidence,
                    confidence=0.99,
                    notes=(
                        f"Annual OC fee locked from exact certificate phrase: "
                        f"'current annual fees for the lot total {fp_str}'. "
                        f"Fast-path match -- no candidate scoring required."
                    ),
                )
            )
            print(f"\n  +- OC FAST-PATH [{doc.filename}]: {fp_str} (conf=0.99)")
            print(f"  |  Evidence: {fp_evidence[:120]}")
            print(f"  +---------------------------------------------")
            return facts
        except (ValueError, AttributeError):
            pass  # fall through to full candidate engine

    # --- Build page list for block splitter ---
    page_texts: list[str] = []
    if doc.pages:
        page_texts = [p.text or p.normalized_text or "" for p in doc.pages]

    # --- Step 1: Split into semantic blocks ---
    blocks = _split_blocks(text, pages=page_texts if page_texts else None)

    # --- Step 2 & 3: Extract candidates from every block ---
    all_candidates: list[OcCandidate] = []
    for block in blocks:
        all_candidates.extend(_extract_candidates_from_block(block))

    # --- Step 4: Select winner ---
    eligible = [c for c in all_candidates if not c.ignored]
    winner: OcCandidate | None = None
    if eligible:
        winner = max(eligible, key=lambda c: c.overall_score)
        # Require at least a levy_schedule-level block or better
        if winner.overall_score < _BLOCK_TYPE_RANK["levy_schedule"] * 0.5:
            winner = None  # not confident enough

    # --- Omni perception sub-agent (fallback) ---
    omni_amount: float | None = None
    omni_quote_str = ""
    if ai_client is not None and should_call_omni(
        best_source="cert_fee_block" if winner else None,
        best_value=winner.annual_amount if winner else None,
        conflict=False,
        suspicious=winner is None,
        from_balance=False,
        lower_than_balance=False,
        unknown_table_shape=False,
        candidate_count=len(eligible),
    ):
        omni_reason = (
            f"winner={'none' if not winner else winner.annual_str} "
            f"candidates={len(eligible)}"
        )
        omni_rows = classify_table_with_omni(
            text=text,
            ai_client=ai_client,
            doc_type="oc",
        )
        omni_result = annual_from_omni_rows(omni_rows)
        _model_name = getattr(ai_client, "model", "unknown")
        if omni_result:
            omni_val, omni_quote_str = omni_result
            omni_ok, _ = validate_omni_candidate(omni_val, omni_rows, set())
            if omni_ok and (winner is None or omni_val > 0):
                omni_amount = omni_val
        omni_write_debug_log(
            filename=doc.filename or "",
            doc_type="oc",
            called=True,
            reason=omni_reason,
            model=_model_name,
            rows=omni_rows,
            candidate=omni_amount,
            accepted=omni_amount is not None,
        )

    # --- Step 5: Emit annual amount fact ---
    if omni_amount is not None and winner is None:
        # Omni rescued a case where the rule engine found nothing
        omni_str = f"${omni_amount:,.2f}"
        facts.append(
            _make_fact(
                doc, P.RATES_OC_ANNUAL_AMOUNT, omni_str,
                omni_quote_str[:300],
                confidence=0.82,
                notes=(
                    f"Annual OC fee from Nemotron Omni perception sub-agent: {omni_str}. "
                    f"No rule-based candidate reached confidence threshold. Verify manually."
                ),
            )
        )
        facts.append(
            _make_fact(
                doc, "rates.oc.needs_review", True,
                omni_quote_str[:300],
                confidence=0.99,
                notes="OC amount from Omni sub-agent only — rule engine found no confident candidate.",
            )
        )

    if winner:
        mult_note = (
            f"{winner.frequency} ${winner.source_amount:,.2f} x {winner.multiplier}"
            if winner.multiplier > 1
            else f"annual ${winner.source_amount:,.2f}"
        )
        ignored_log = [
            {"amount": f"${c.source_amount:,.2f}", "reason": c.ignore_reason}
            for c in all_candidates if c.ignored
        ]
        ignored_note = (
            f" Ignored: {ignored_log}" if ignored_log else ""
        )

        # Confidence: winner block type rank mapped to 0-0.99
        rank = _BLOCK_TYPE_RANK.get(winner.block_type, 0)
        confidence = min(0.99, 0.60 + (rank / 100) * 0.39) * winner.freq_confidence

        facts.append(
            _make_fact(
                doc, P.RATES_OC_ANNUAL_AMOUNT, winner.annual_str,
                winner.evidence[:300],
                confidence=round(confidence, 3),
                notes=(
                    f"Annual OC fee: {mult_note} = {winner.annual_str}. "
                    f"Block type: {winner.block_type} (score={winner.block_score}). "
                    f"Frequency: {winner.frequency}.{ignored_note}"
                ),
            )
        )

    _oc_debug_log(doc, blocks, all_candidates, winner)

    return facts
