"""Policy defaults — always-fixed facts that every matter uses.

These facts do NOT depend on any document — they represent firm-wide policy
decisions about how the Sec 32 form should always be filled:

  Tab 1:
    - "Their amounts are:"                        always CHECKED
    - "Are contained in attached certificate(s)"  always CHECKED
    - "Their total does not exceed"               always CHECKED

  Tab 2:
    - "Is in the attached copies of title documents"                     always CHECKED
    - "Particulars of any existing failure to comply..."                 always CHECKED
    - The failure-to-comply text                                         always standard text
    - "Certificate with required information attached" (planning scheme) always CHECKED

  Tab 6:
    - Due Diligence Checklist field                                      always "Is attached"

All facts carry confidence=1.0 and extractor="rule:policy_defaults_v1".
"""
from __future__ import annotations

from datetime import datetime

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source

EXTRACTOR_NAME = "rule:policy_defaults_v1"

_FAILURE_TO_COMPLY_STANDARD_TEXT = (
    "To the best of the Vendor's knowledge there is no existing failure to comply "
    "with the terms of any easements, covenants or other similar restriction."
)


def _make_fact(path: str, value: object, note: str) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=1.0,
        sources=[
            Source(
                file="policy_defaults",
                quote=note,
                quote_verified=True,
                extractor_note=note,
            )
        ],
        extractor=EXTRACTOR_NAME,
        extracted_at=datetime.utcnow(),
        notes=note,
    )


def inject_default_policy_facts(store) -> None:  # type: ignore[type-arg]
    """Inject all always-fixed policy facts into the FactStore."""
    facts: list[Fact] = [
        # ── Tab 1 always-checked items ────────────────────────────────────
        _make_fact(
            P.POLICY_TAB1_AMOUNTS_ARE_CHECKED, True,
            "Firm policy: 'Their amounts are:' is always checked in Tab 1",
        ),
        _make_fact(
            P.POLICY_TAB1_CERTS_ATTACHED, True,
            "Firm policy: 'Are contained in attached certificate(s)' is always checked",
        ),
        _make_fact(
            P.POLICY_TAB1_TOTAL_DOES_NOT_EXCEED, True,
            "Firm policy: 'Their total does not exceed' is always checked",
        ),

        # ── Tab 2 always-checked items ────────────────────────────────────
        _make_fact(
            P.POLICY_TAB2_TITLE_IN_ATTACHED, True,
            "Firm policy: 'Is in the attached copies of title documents' is always checked",
        ),
        _make_fact(
            P.POLICY_TAB2_FAILURE_CHECKED, True,
            "Firm policy: 'Particulars of any existing failure to comply...' is always checked",
        ),
        _make_fact(
            P.POLICY_TAB2_FAILURE_TEXT,
            _FAILURE_TO_COMPLY_STANDARD_TEXT,
            "Firm standard text for failure-to-comply disclosure",
        ),
        _make_fact(
            P.POLICY_TAB2_PLANNING_CERT_ATTACHED, True,
            "Firm policy: 'Certificate with required information attached' is always checked",
        ),

        # ── Tab 6 Due Diligence ───────────────────────────────────────────
        _make_fact(
            P.POLICY_TAB6_DUE_DILIGENCE_TEXT, "Is attached",
            "Firm policy: Due Diligence Checklist field always says 'Is attached'",
        ),
    ]
    store.add_many(facts)
