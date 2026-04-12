"""Policy computed facts — derived from FactStore after Brain A runs.

Currently computes:
  1. Annualisation verification: compare vendor-form water rate vs cert × 4.
     Emits policy.verification.water_amount_match (True/False).

  2. Attachment list text for Tab 6 field 13.
     Emits policy.attachments.text.

Facts emitted here have extractor="rule:policy_computed_v1".
"""
from __future__ import annotations

from datetime import datetime

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.policy.attachments import build_attachments_text
from triconvey_agent.canonical.schemas import Fact, Source

EXTRACTOR_NAME = "rule:policy_computed_v1"


def _make_fact(path: str, value: object, note: str, *, confidence: float = 0.97) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[
            Source(
                file="policy_computed",
                quote=note,
                quote_verified=True,
                extractor_note=note,
            )
        ],
        extractor=EXTRACTOR_NAME,
        extracted_at=datetime.utcnow(),
        notes=note,
    )


def _parse_dollar(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(str(s).replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _first_money_value(store, path: str, *, preferred_extractors: tuple[str, ...] = ()) -> float | None:  # type: ignore[type-arg]
    facts = store.get_all(path)
    if not facts:
        return None

    for extractor in preferred_extractors:
        for fact in facts:
            if fact.extractor == extractor:
                parsed = _parse_dollar(str(fact.value))
                if parsed is not None:
                    return parsed

    winner = store.get_value(path)
    parsed_winner = _parse_dollar(str(winner)) if winner is not None else None
    if parsed_winner is not None:
        return parsed_winner

    for fact in facts:
        parsed = _parse_dollar(str(fact.value))
        if parsed is not None:
            return parsed

    return None


def _compute_tab1_total_amount(store) -> list[Fact]:  # type: ignore[type-arg]
    """Compute the amount for 'Their total does not exceed'.

    Rule requested by the user:
      council + water authority + state revenue land tax (if any)
      + owners corporation (if any) + 1000
    """
    council = _first_money_value(store, P.RATES_COUNCIL_ANNUAL) or 0.0
    water = _first_money_value(store, P.RATES_WATER_ANNUAL) or 0.0
    land_tax = _first_money_value(
        store,
        P.RATES_LAND_TAX_AMOUNT,
        preferred_extractors=("rule:land_tax_certificate_v1",),
    ) or 0.0
    owners_corporation = _first_money_value(store, P.RATES_OC_ANNUAL_AMOUNT) or 0.0

    total = council + water + land_tax + owners_corporation + 1000.0
    total_str = f"${total:,.2f}"
    note = (
        f"Computed as council ${council:,.2f} + water ${water:,.2f} + "
        f"land tax ${land_tax:,.2f} + owners corporation ${owners_corporation:,.2f} "
        f"+ $1,000.00 buffer = {total_str}"
    )
    return [
        _make_fact(
            P.POLICY_TAB1_TOTAL_DOES_NOT_EXCEED_AMOUNT,
            total_str,
            note,
            confidence=0.95,
        )
    ]


def _verify_water_amounts(store) -> list[Fact]:  # type: ignore[type-arg]
    """Compare vendor-form annual water rate against cert quarterly × 4.

    Emits:
      policy.verification.water_amount_match = True   (within 10%)
                                               False  (discrepancy > 10%)
    """
    vendor_fact, _ = store.get(P.RATES_WATER_ANNUAL)
    cert_annual_fact, _ = store.get(P.RATES_WATER_CERT_AMOUNT_ANNUAL)

    if vendor_fact is None or cert_annual_fact is None:
        return []

    vendor_amt = _parse_dollar(str(vendor_fact.value))
    cert_amt = _parse_dollar(str(cert_annual_fact.value))

    if vendor_amt is None or cert_amt is None:
        return []

    # If vendor source is the cert itself (confidence 0.75), skip verification
    if vendor_fact.extractor == "rule:water_authority_certificate_v1":
        return [
            _make_fact(
                P.POLICY_WATER_AMOUNT_VERIFIED, None,
                "Vendor-form water rate not found; using cert estimate — verify annually",
                confidence=0.50,
            )
        ]

    diff_pct = abs(vendor_amt - cert_amt) / max(cert_amt, 1.0) * 100
    match = diff_pct <= 10.0
    note = (
        f"Vendor-form annual ${vendor_amt:,.2f} vs cert quarterly×4 ${cert_amt:,.2f} "
        f"— diff {diff_pct:.1f}% — {'OK' if match else 'DISCREPANCY'}"
    )
    return [
        _make_fact(
            P.POLICY_WATER_AMOUNT_VERIFIED, match, note,
            confidence=0.97 if match else 0.50,
        )
    ]


def _build_attachments(store) -> list[Fact]:  # type: ignore[type-arg]
    """Build the attachment list text and emit it as a fact."""
    try:
        text = build_attachments_text(store)
    except Exception as exc:
        text = f"[ERROR building attachment list: {exc}]"

    return [
        _make_fact(
            P.POLICY_ATTACHMENTS_TEXT,
            text,
            "Auto-built from extracted document dates, names, and identifiers",
            confidence=0.90,  # requires human review of ## items
        )
    ]


def inject_computed_facts(store) -> None:  # type: ignore[type-arg]
    """Run all computed-fact generators and inject results into the FactStore."""
    store.add_many(_verify_water_amounts(store))
    store.add_many(_compute_tab1_total_amount(store))
    store.add_many(_build_attachments(store))
