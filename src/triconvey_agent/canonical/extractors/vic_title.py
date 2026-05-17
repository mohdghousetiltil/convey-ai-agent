"""Canonical Victorian title extractor — emits Fact objects.

Reads a Register Search Statement (Copy of Title) PDF and produces Facts
at canonical title.* paths. Also writes overlapping property.* paths so
the FactStore's authority resolver can prefer title-derived values over
the vendor form's recollection.

This extractor is registered as `rule:vic_title_v1` and matches the
`rule:vic_title*` glob in DEFAULT_AUTHORITY_RULES.
"""
from __future__ import annotations

import re

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:vic_title_v1"

# ---------------------------------------------------------------------------
# Anchors / regex
# ---------------------------------------------------------------------------

# Volume/folio header on the very first line.
_VOLUME_FOLIO_RE = re.compile(r"VOLUME\s+(\d+)\s+FOLIO\s+(\d+)", re.IGNORECASE)

# Security number — appears once on the first page header.
_SECURITY_RE = re.compile(r"Security\s+no\s*:\s*([A-Z0-9]+)", re.IGNORECASE)

# Produced timestamp.
_PRODUCED_RE = re.compile(
    r"Produced\s+(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s*(?:AM|PM))",
    re.IGNORECASE,
)

# Land description block — terminates at PARENT TITLE.
_LAND_DESC_RE = re.compile(
    r"LAND DESCRIPTION\s+(.+?)\s+PARENT TITLE",
    re.IGNORECASE | re.DOTALL,
)

# Parent title volume/folio.
_PARENT_TITLE_RE = re.compile(
    r"PARENT TITLE\s+(Volume\s+(\d+)\s+Folio\s+(\d+))",
    re.IGNORECASE,
)

# "Created by instrument <num> <date>" — captures both.
_CREATED_BY_RE = re.compile(
    r"Created by instrument\s+([A-Z0-9]+)\s+(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)

# Plan info embedded in land description, e.g. "Plan of Subdivision PS826454D" or "Plan of Subdivision 826454D".
_PLAN_RE = re.compile(
    r"(Plan of Consolidation|Plan of Subdivision|Plan of Survey|Title Plan)\s+([A-Z0-9]+)",
    re.IGNORECASE,
)

_PLAN_TYPE_TO_PREFIX: dict[str, str] = {
    "plan of subdivision": "PS",
    "plan of survey": "PS",
    "plan of consolidation": "PC",
    "title plan": "TP",
}
_KNOWN_PLAN_PREFIXES = ("PS", "PC", "LP", "TP", "RP", "SP", "AL")


def _normalize_plan_number(plan_type: str, number: str) -> str:
    """Ensure plan number has the correct prefix (e.g. '502358G' → 'PS502358G')."""
    upper = number.strip().upper()
    if any(upper.startswith(p) for p in _KNOWN_PLAN_PREFIXES):
        return upper
    prefix = _PLAN_TYPE_TO_PREFIX.get(plan_type.strip().lower(), "")
    return f"{prefix}{upper}" if prefix else upper

# Lot number, e.g. "Lot 1620 on ...".
_LOT_RE = re.compile(r"Lot\s+([A-Z0-9]+)\s+on\s+", re.IGNORECASE)

# Registered proprietor block. Captures estate, tenancy, name+address,
# dealing number, dealing date.
_PROPRIETOR_BLOCK_RE = re.compile(
    r"REGISTERED PROPRIETOR\s+"
    r"Estate\s+(?P<estate>Fee Simple|Leasehold|Crown Lease)\s+"
    r"(?P<tenancy>Sole Proprietor|Joint Proprietors?|Tenants in Common)\s+"
    r"(?P<body>.+?)\s+ENCUMBRANCES",
    re.IGNORECASE | re.DOTALL,
)

# A single proprietor entry inside the body, e.g.
#   "ERIN KRISTINE SELLECK of 43 SOUTHAMPTON DRIVE MULGRAVE VIC 3170
#       AU146757D 17/03/2021"
_PROPRIETOR_ENTRY_RE = re.compile(
    r"([A-Z][A-Z\s\-']+?)\s+of\s+(.+?)\s+([A-Z]{2}\d+[A-Z]?)\s+(\d{2}/\d{2}/\d{4})",
    re.DOTALL,
)

# Encumbrances section terminates at the DIAGRAM LOCATION section header
# (which is the phrase "DIAGRAM LOCATION" on its own line). The phrase may
# also appear inline inside a covenant body ("...as set out under DIAGRAM
# LOCATION below.") so the boundary must be a line-start match.
_ENCUMBRANCES_BLOCK_RE = re.compile(
    r"ENCUMBRANCES, CAVEATS AND NOTICES\s*\n(.+?)\n[ \t]*DIAGRAM LOCATION[ \t]*\n",
    re.IGNORECASE | re.DOTALL,
)

# A typed encumbrance entry: TYPE, dealing number, date.
# Dealing number shapes:
#   [A-Z]{1,3}\d+[A-Z]?  covers AW439698S, BA088550M, H496232 (single-letter prefix)
#   \d{4,}[A-Z]?         covers pure-numeric instruments like 1489008
# Two-letter minimum was historically used to exclude "Section 47" noise; we
# now rely on the keyword anchor (MORTGAGE|COVENANT|…) to prevent false matches.
_ENCUMBRANCE_HEAD_RE = re.compile(
    r"(MORTGAGE|COVENANT|CAVEAT|AGREEMENT|SECTION 173 AGREEMENT|NOTICE)"
    r"(?:\s+([A-Z]{1,3}\d+[A-Z]?|\d{4,}[A-Z]?)\b)?"
    r"(?:\s+(\d{2}/\d{2}/\d{4}))?",
)

_DEALING_IN_BODY_RE = re.compile(r"\b([A-Z]{1,3}\d+[A-Z]?|\d{4,}[A-Z]?)\s+(\d{2}/\d{2}/\d{4})\b")

_DIAGRAM_LOCATION_RE = re.compile(
    r"\n[ \t]*DIAGRAM LOCATION[ \t]*\n(.+?)\n[ \t]*ACTIVITY IN THE LAST",
    re.IGNORECASE | re.DOTALL,
)

_STREET_ADDRESS_RE = re.compile(
    r"Street Address:\s*(.+?)(?:\r?\n|$)",
    re.IGNORECASE,
)

_ECT_CONTROL_RE = re.compile(
    r"eCT Control\s+(.+?)\s+Effective from\s+(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE | re.DOTALL,
)

# Owners Corporations section — page 2 of the title.
# Matches: "The land in this folio is affected by\n  OWNERS CORPORATION 1 PLAN NO. PS723695D"
_OWNERS_CORP_SECTION_RE = re.compile(
    r"OWNERS\s+CORPORATIONS?\s+The\s+land\s+in\s+this\s+folio\s+is\s+affected\s+by\s+"
    r"((?:OWNERS\s+CORPORATION\s+\S+(?:\s+PLAN\s+NO\.\s+\S+)?\s*)+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compact(value: str) -> str:
    """Collapse internal whitespace to single spaces."""
    return " ".join(value.split())


def _doc_text(doc: Document) -> str:
    return doc.raw_text or doc.normalized_text or ""


def _make_source(doc: Document, quote: str) -> Source:
    return Source(
        file=doc.filename,
        page=None,
        quote=quote,
        quote_verified=True,
    )


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


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------

def _extract_volume_folio(doc: Document, text: str) -> list[Fact]:
    m = _VOLUME_FOLIO_RE.search(text)
    if not m:
        return []
    volume = m.group(1)
    folio = m.group(2)
    quote = m.group(0)
    combined = f"Volume {volume} Folio {folio}"
    return [
        _make_fact(doc, P.TITLE_VOLUME_FOLIO, combined, quote, confidence=0.99),
        _make_fact(doc, P.TITLE_VOLUME, volume, quote, confidence=0.99),
        _make_fact(doc, P.TITLE_FOLIO, folio, quote, confidence=0.99),
        _make_fact(doc, P.PROPERTY_VOLUME_OR_BOOK, volume, quote, confidence=0.99),
        _make_fact(doc, P.PROPERTY_FOLIO_OR_NUMBER, folio, quote, confidence=0.99),
    ]


def _extract_header_meta(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    if m := _SECURITY_RE.search(text):
        facts.append(
            _make_fact(doc, P.TITLE_SECURITY_NUMBER, m.group(1), m.group(0))
        )
    if m := _PRODUCED_RE.search(text):
        facts.append(
            _make_fact(doc, P.TITLE_PRODUCED_AT, m.group(1), m.group(0))
        )
    return facts


def _extract_land_description(doc: Document, text: str) -> list[Fact]:
    m = _LAND_DESC_RE.search(text)
    if not m:
        return []
    description = _compact(m.group(1))
    quote = _compact(m.group(0))
    facts = [
        _make_fact(doc, P.TITLE_LAND_DESCRIPTION, description, quote),
    ]

    if plan_match := _PLAN_RE.search(description):
        plan_type = _compact(plan_match.group(1))
        raw_num = _compact(plan_match.group(2))
        plan_number = _normalize_plan_number(plan_type, raw_num)

        # If the raw number had no prefix (e.g. "121955"), the normalizer
        # guesses PS.  Cross-check with "Created by instrument LP121955" —
        # if the instrument carries a more specific prefix for the same
        # numeric value, use that instead (LP beats PS for licensed plans).
        if not any(raw_num.upper().startswith(p) for p in _KNOWN_PLAN_PREFIXES):
            instr_m = _CREATED_BY_RE.search(text)
            if instr_m:
                instr = instr_m.group(1).upper()
                instr_digits = re.sub(r"[^0-9]", "", instr)
                raw_digits = re.sub(r"[^0-9]", "", raw_num)
                if raw_digits and instr_digits == raw_digits and any(
                    instr.startswith(p) for p in _KNOWN_PLAN_PREFIXES
                ):
                    plan_number = instr
        facts.append(
            _make_fact(doc, P.TITLE_PLAN_TYPE, plan_type, quote)
        )
        facts.append(
            _make_fact(doc, P.TITLE_PLAN_NUMBER, plan_number, quote)
        )
        facts.append(
            _make_fact(doc, P.PROPERTY_PLAN_NUMBER, plan_number, quote, confidence=0.98)
        )

    if lot_match := _LOT_RE.search(description):
        lot = lot_match.group(1)
        facts.append(_make_fact(doc, P.TITLE_LOT_NUMBER, lot, quote))
        facts.append(
            _make_fact(doc, P.PROPERTY_LOT_NUMBER, lot, quote, confidence=0.98)
        )

    return facts


def _extract_parent_title(doc: Document, text: str) -> list[Fact]:
    m = _PARENT_TITLE_RE.search(text)
    if not m:
        return []
    parent = _compact(m.group(1))
    quote = m.group(0)
    return [
        _make_fact(doc, P.TITLE_PARENT_TITLE, parent, quote),
        _make_fact(doc, P.TITLE_PARENT_VOLUME, m.group(2), quote),
        _make_fact(doc, P.TITLE_PARENT_FOLIO, m.group(3), quote),
    ]


def _extract_created_by(doc: Document, text: str) -> list[Fact]:
    m = _CREATED_BY_RE.search(text)
    if not m:
        return []
    return [
        _make_fact(doc, P.TITLE_CREATED_BY_INSTRUMENT, m.group(1), m.group(0)),
        _make_fact(doc, P.TITLE_CREATED_AT, m.group(2), m.group(0)),
    ]


def _extract_proprietors(doc: Document, text: str) -> list[Fact]:
    m = _PROPRIETOR_BLOCK_RE.search(text)
    if not m:
        return []

    estate = _compact(m.group("estate"))
    tenancy = _compact(m.group("tenancy"))
    body = m.group("body")
    block_quote = _compact(m.group(0))

    facts: list[Fact] = [
        _make_fact(doc, P.TITLE_ESTATE, estate, block_quote),
        _make_fact(doc, P.TITLE_TENANCY, tenancy, block_quote),
    ]

    entries = list(_PROPRIETOR_ENTRY_RE.finditer(body))
    for idx, entry in enumerate(entries):
        name = _compact(entry.group(1))
        address = _compact(entry.group(2))
        dealing = _compact(entry.group(3))
        dealing_date = _compact(entry.group(4))
        entry_quote = _compact(entry.group(0))

        facts.append(_make_fact(doc, P.title_proprietor(idx, "name"), name, entry_quote))
        facts.append(_make_fact(doc, P.title_proprietor(idx, "address"), address, entry_quote))
        facts.append(_make_fact(doc, P.title_proprietor(idx, "dealing"), dealing, entry_quote))
        facts.append(_make_fact(doc, P.title_proprietor(idx, "dealing_date"), dealing_date, entry_quote))

    facts.append(
        _make_fact(
            doc,
            P.TITLE_PROPRIETOR_COUNT,
            len(entries),
            block_quote,
            confidence=0.99,
            notes=f"counted {len(entries)} entries inside REGISTERED PROPRIETOR block",
        )
    )
    return facts


def _split_encumbrance_entries(block: str) -> list[tuple[str, str]]:
    """Split the encumbrances block into (head_line, body) tuples.

    Each encumbrance starts with a TYPE keyword on its own line; subsequent
    indented lines belong to that entry.
    """
    lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
    entries: list[tuple[str, list[str]]] = []
    for line in lines:
        stripped = line.lstrip()
        starts_new = bool(_ENCUMBRANCE_HEAD_RE.match(stripped)) and not line.startswith(" " * 4)
        if starts_new:
            entries.append((stripped, []))
        elif entries:
            entries[-1][1].append(stripped)
    return [(head, "\n".join([head, *body]).strip()) for head, body in entries]


def _extract_encumbrances(doc: Document, text: str) -> list[Fact]:
    m = _ENCUMBRANCES_BLOCK_RE.search(text)
    if not m:
        # Still emit "no encumbrances" booleans? Skip — silence is safer here.
        return []
    block = m.group(1)

    facts: list[Fact] = []
    has_mortgage = False
    has_covenant = False
    has_caveat = False
    has_notice = False
    has_s173 = False

    entries = _split_encumbrance_entries(block)
    for idx, (head_line, full_quote) in enumerate(entries):
        head = _ENCUMBRANCE_HEAD_RE.match(head_line)
        if not head:
            continue
        e_type = head.group(1).upper()
        e_number = head.group(2) or ""
        e_date = head.group(3) or ""

        # Body text after the head line — typically the party or details.
        body_lines = full_quote.splitlines()[1:]
        if (not e_number or not e_date) and body_lines:
            body_dealing = _DEALING_IN_BODY_RE.search(body_lines[0])
            if body_dealing:
                e_number = e_number or body_dealing.group(1)
                e_date = e_date or body_dealing.group(2)
        body_text = _compact(" ".join(body_lines))

        facts.append(_make_fact(doc, P.title_encumbrance(idx, "type"), e_type, full_quote))
        if e_number:
            facts.append(_make_fact(doc, P.title_encumbrance(idx, "number"), e_number, full_quote))
        if e_date:
            facts.append(_make_fact(doc, P.title_encumbrance(idx, "date"), e_date, full_quote))
        if body_text:
            facts.append(_make_fact(doc, P.title_encumbrance(idx, "party"), body_text, full_quote))
        facts.append(_make_fact(doc, P.title_encumbrance(idx, "text"), _compact(full_quote), full_quote))

        if e_type == "MORTGAGE":
            has_mortgage = True
        elif e_type == "COVENANT":
            has_covenant = True
        elif e_type == "CAVEAT":
            has_caveat = True
        elif e_type == "NOTICE":
            has_notice = True
        elif "SECTION 173" in e_type or "AGREEMENT" == e_type:
            has_s173 = True

    block_quote = _compact(m.group(0))[:400]
    facts.append(
        _make_fact(
            doc, P.TITLE_ENCUMBRANCE_COUNT, len(entries), block_quote, confidence=0.99
        )
    )
    facts.append(_make_fact(doc, P.TITLE_HAS_MORTGAGE, has_mortgage, block_quote))
    facts.append(_make_fact(doc, P.TITLE_HAS_COVENANT, has_covenant, block_quote))
    facts.append(_make_fact(doc, P.TITLE_HAS_CAVEAT, has_caveat, block_quote))
    facts.append(_make_fact(doc, P.TITLE_HAS_NOTICE, has_notice, block_quote))
    facts.append(_make_fact(doc, P.TITLE_HAS_SECTION_173_AGREEMENT, has_s173, block_quote))
    return facts


def _extract_diagram_location(doc: Document, text: str) -> list[Fact]:
    m = _DIAGRAM_LOCATION_RE.search(text)
    if not m:
        return []
    return [
        _make_fact(doc, P.TITLE_DIAGRAM_LOCATION, _compact(m.group(1)), _compact(m.group(0)))
    ]


def _extract_street_address(doc: Document, text: str) -> list[Fact]:
    m = _STREET_ADDRESS_RE.search(text)
    if not m:
        return []
    address = _compact(m.group(1))
    quote = _compact(m.group(0))
    return [
        _make_fact(doc, P.TITLE_STREET_ADDRESS, address, quote),
        # Also register against the canonical property.address — title is
        # authoritative for the legal address.
        _make_fact(doc, P.PROPERTY_ADDRESS, address, quote, confidence=0.95,
                   notes="title's 'Street Address' (additional info)"),
    ]


def _extract_owners_corporations(doc: Document, text: str) -> list[Fact]:
    m = _OWNERS_CORP_SECTION_RE.search(text)
    if not m:
        return []
    names_block = _compact(m.group(1))
    quote = _compact(m.group(0))
    # Collect individual OC identifiers (e.g. "OWNERS CORPORATION 1 PLAN NO. PS723695D")
    oc_entries = re.findall(
        r"OWNERS\s+CORPORATION\s+(\S+(?:\s+PLAN\s+NO\.\s+\S+)?)",
        names_block,
        re.IGNORECASE,
    )
    return [
        _make_fact(doc, P.TITLE_HAS_OWNERS_CORPORATION, True, quote, confidence=0.99),
        _make_fact(doc, P.TITLE_OWNERS_CORPORATION_NAMES, oc_entries, quote, confidence=0.99),
    ]


def _extract_ect_control(doc: Document, text: str) -> list[Fact]:
    m = _ECT_CONTROL_RE.search(text)
    if not m:
        return []
    controller = _compact(m.group(1))
    effective = m.group(2)
    quote = _compact(m.group(0))
    return [
        _make_fact(doc, P.TITLE_ECT_CONTROLLER, controller, quote),
        _make_fact(doc, P.TITLE_ECT_EFFECTIVE_FROM, effective, quote),
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_vic_title_facts(doc: Document) -> list[Fact]:
    """Extract every supported field from a Victorian title PDF.

    Returns an empty list when the document is not a register search
    statement.
    """
    text = _doc_text(doc)
    if not text:
        return []

    # Conservative guard — every register search statement contains the
    # phrase "REGISTER SEARCH STATEMENT" in the page footer.
    if "REGISTER SEARCH STATEMENT" not in text.upper():
        return []

    facts: list[Fact] = []
    facts.extend(_extract_volume_folio(doc, text))
    facts.extend(_extract_header_meta(doc, text))
    facts.extend(_extract_land_description(doc, text))
    facts.extend(_extract_parent_title(doc, text))
    facts.extend(_extract_created_by(doc, text))
    facts.extend(_extract_proprietors(doc, text))
    facts.extend(_extract_encumbrances(doc, text))
    facts.extend(_extract_diagram_location(doc, text))
    facts.extend(_extract_street_address(doc, text))
    facts.extend(_extract_ect_control(doc, text))
    oc_facts = _extract_owners_corporations(doc, text)
    facts.extend(oc_facts)
    # If this is a valid RSS (has Volume/Folio) but no OC section found, emit explicit False
    # so vendor-form fallback cannot wrongly tick the OC checkbox.
    has_oc_fact = any(f.path == P.TITLE_HAS_OWNERS_CORPORATION for f in oc_facts)
    if not has_oc_fact and _VOLUME_FOLIO_RE.search(text):
        facts.append(
            _make_fact(doc, P.TITLE_HAS_OWNERS_CORPORATION, False, "no OWNERS CORPORATIONS section found", confidence=0.95)
        )
    return facts
