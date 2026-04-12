"""Canonical vendor form extractor — emits Fact objects.

This is the first Brain A extractor written against the new architecture.
It is a pure function:

    extract_vendor_form_facts(doc) -> list[Fact]

Every Fact carries a verbatim quote from the document text, so the
FactStore (and any reviewer) can always trace a value back to its source.

Differences from legacy extractors/vendor_form.py
--------------------------------------------------
- Returns Facts at canonical paths instead of mutating a FinalExtraction
- Multi-vendor support: paths use vendor.0.*, vendor.1.* etc.
- Indexed sub-fields for trust, insurance, services
- Uses verbatim quotes for evidence (anti-hallucination guard)
- Confidence levels reflect extraction certainty:
    0.97  exact label match, single answer line
    0.92  services-checklist match (substring detection)
    0.85  weak match (label found but value ambiguous)
"""
from __future__ import annotations

import re

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

# ---------------------------------------------------------------------------
# Identification
# ---------------------------------------------------------------------------

EXTRACTOR_NAME = "rule:vendor_form_v2"
"""Stable extractor identifier — used by AuthorityRules to rank facts."""


# ---------------------------------------------------------------------------
# Form structure
# ---------------------------------------------------------------------------

# All known labels in this form. Used by the line scanner to detect when
# a value section ends (the next label appears).
KNOWN_LABELS: set[str] = {
    "What is your title?",
    "Your first name",
    "Your middle name",
    "Your last name",
    "Preferred Name",
    "Occupation",
    "Residential address",
    "Overseas address if applicable",
    "Contact Number",
    "Email Address",
    "Birth date",
    "Gender",
    "Country of citizenship",
    "Nationality",
    "Have we acted for you before?",
    "How did you hear about us?",
    "Is the Property held by a Trust?",
    "Name of Trust",
    "Please confirm Trust Type",
    "Who are the beneficiaries?",
    "Trust ABN",
    "Property Address",
    "Nature of property",
    "Approximate age of Dwelling / Building",
    "Volume or book:",
    "Folio or number:",
    "Lot number",
    "Plan number",
    "Do you live at the property?",
    "Is the Property rented/leased?",
    "Is the property insured?",
    "Council",
    "Council rates",
    "Water Authority",
    "Water rates",
    "Do you pay land tax?",
    "Is any vacant land residential land tax payable?",
    "Is the land in a bushfire prone area?",
    "Is the land affected by any planning overlays?",
    "Is there access to the property by road?",
    "Have any building permits been issued in the last 7 years?",
    "Has there been any building works/structures undertaken in the last 7 years?",
    "Have you done any works without obtaining a permit?",
    "Has any building work been done on the land that DID NOT have the required building nor planning approvals?",
    "Have you improved the property within the past 6.5 years?",
    "Have you installed new appliances where works relating to gas, electrical and plumbing have been undertaken?",
    "Have you obtained the Compliance certificates - as per S.137 (c) of the Building Act",
    "Does the property have a water tank?",
    "Was a permit required for the water tank",
    "Please provide full particulars of your insurance cover:",
    "Please upload Certificate of Currency",
    "Section 27 - Do you want/need an early release of deposit?",
}

# Labels that mark a vendor block boundary (the form repeats this label
# for each additional vendor).
_VENDOR_BLOCK_START_LABEL = "What is your title?"

# Service variants from the connected-services checklist.
# Each entry: (canonical_name, display_label, lowercased substrings to detect)
_SERVICE_VARIANTS: list[tuple[str, str, list[str]]] = [
    ("electricity", "Mains Electricity", ["mains electricity"]),
    ("gas",         "Mains Gas",         ["mains gas"]),
    ("water",       "Mains Water",       ["mains water"]),
    ("sewerage",    "Mains Sewerage",    ["mains sewerage", "sewerage"]),
    ("septic_tank", "Septic Tank",       ["septic tank"]),
    ("bottled_gas", "Bottled Gas",       ["bottled gas", "lpg"]),
    ("tank_water",  "Tank Water",        ["tank water"]),
    ("telephone",   "Telephone",         ["telephone"]),
    ("nbn",         "NBN",               ["nbn", "voip"]),
]

_SERVICES_HEADER_PATTERN = re.compile(
    r"services\s+listed\s+connected\s+to\s+the\s+land", re.IGNORECASE
)

_NEGATIVE_SERVICE_PATTERNS = (
    "not connected",
    "not available",
)


# ---------------------------------------------------------------------------
# Low-level scanning helpers
# ---------------------------------------------------------------------------

def _normalise_pdf_chars(text: str) -> str:
    """Replace ligature/odd unicode used in some PDFs with plain ASCII."""
    return (
        text.replace("\ufb01", "fi")  # ﬁ
        .replace("\ufb02", "fl")      # ﬂ
        .replace("\u2018", "'").replace("\u2019", "'")
        .replace("\u201c", '"').replace("\u201d", '"')
    )


def _doc_text(doc: Document) -> str:
    return _normalise_pdf_chars(doc.raw_text or doc.normalized_text or "")


def _doc_lines(doc: Document) -> list[str]:
    return [line.rstrip() for line in _doc_text(doc).splitlines()]


def _find_value_after_label(
    lines: list[str],
    label: str,
    *,
    max_value_lines: int = 1,
    start_at: int = 0,
) -> tuple[str | None, int, int]:
    """Find a label and return (value, label_line_idx, value_end_idx).

    The label match is exact (after stripping whitespace).
    The value is the first non-empty line after the label that is NOT
    itself one of the KNOWN_LABELS. Returns (None, -1, -1) on miss.
    """
    target = label.strip()
    for i in range(start_at, len(lines)):
        if lines[i].strip() != target:
            continue
        # Walk forward looking for value lines
        value_parts: list[str] = []
        end_idx = i
        for j in range(i + 1, min(i + 1 + max_value_lines + 4, len(lines))):
            v = lines[j].strip()
            if not v:
                if value_parts:
                    break
                continue
            if v in KNOWN_LABELS:
                break
            if v.endswith("?") and len(v) > 6:
                break  # next question
            value_parts.append(v)
            end_idx = j
            if len(value_parts) >= max_value_lines:
                break
        if value_parts:
            return " ".join(value_parts), i, end_idx
        return None, i, i
    return None, -1, -1


def _quote_for(lines: list[str], label_idx: int, value_end_idx: int) -> str:
    """Build a verbatim quote spanning the label and value lines."""
    if label_idx < 0:
        return ""
    snippet_lines = lines[label_idx : value_end_idx + 1]
    return "\n".join(snippet_lines).strip()


def _yes_no_to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    norm = value.strip().lower()
    if norm.startswith("yes"):
        return True
    if norm.startswith("no"):
        return False
    return None


def _make_source(doc: Document, quote: str) -> Source:
    return Source(
        file=doc.filename,
        page=None,
        quote=quote,
        quote_verified=True,  # we just read it from the same text
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

def _vendor_block_start_lines(lines: list[str]) -> list[int]:
    """Return the line indexes where each vendor block starts."""
    return [i for i, line in enumerate(lines) if line.strip() == _VENDOR_BLOCK_START_LABEL]


def _extract_vendor_block(
    doc: Document,
    lines: list[str],
    block_start: int,
    block_end: int,
    vendor_index: int,
) -> list[Fact]:
    """Extract one vendor's identity fields from a [block_start, block_end) range."""
    facts: list[Fact] = []
    block = lines[block_start:block_end]

    label_to_fact_field = [
        ("What is your title?",     "title"),
        ("Your first name",          "first_name"),
        ("Your middle name",         "middle_name"),
        ("Your last name",           "last_name"),
        ("Preferred Name",           "preferred_name"),
        ("Occupation",               "occupation"),
        ("Residential address",      "residential_address"),
        ("Contact Number",           "phone"),
        ("Email Address",            "email"),
        ("Country of citizenship",   "country_of_citizenship"),
        ("Nationality",              "nationality"),
    ]

    for label, field in label_to_fact_field:
        value, label_idx, value_end = _find_value_after_label(block, label)
        if value is None or value == "-":
            continue
        quote = _quote_for(block, label_idx, value_end)
        facts.append(_make_fact(doc, P.vendor(vendor_index, field), value, quote))

    return facts


def _extract_vendors(doc: Document, lines: list[str]) -> list[Fact]:
    """Extract every vendor's identity, plus a vendor.count fact."""
    starts = _vendor_block_start_lines(lines)
    if not starts:
        return []

    facts: list[Fact] = []
    # Block i runs from starts[i] up to starts[i+1] (or end of doc).
    for idx, block_start in enumerate(starts):
        block_end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        facts.extend(_extract_vendor_block(doc, lines, block_start, block_end, idx))

    facts.append(
        _make_fact(
            doc,
            P.VENDOR_COUNT,
            len(starts),
            quote=f"{len(starts)} vendor block(s) detected",
            confidence=0.99,
            notes="counted by occurrences of 'What is your title?' label",
        )
    )
    return facts


def _extract_property(doc: Document, lines: list[str]) -> list[Fact]:
    facts: list[Fact] = []
    label_to_path = [
        ("Property Address",                       P.PROPERTY_ADDRESS),
        ("Nature of property",                     P.PROPERTY_NATURE),
        ("Approximate age of Dwelling / Building", P.PROPERTY_DWELLING_AGE),
        ("Volume or book:",                        P.PROPERTY_VOLUME_OR_BOOK),
        ("Folio or number:",                       P.PROPERTY_FOLIO_OR_NUMBER),
        ("Lot number",                             P.PROPERTY_LOT_NUMBER),
        ("Plan number",                            P.PROPERTY_PLAN_NUMBER),
    ]
    for label, path in label_to_path:
        value, label_idx, value_end = _find_value_after_label(lines, label)
        if value is None or value.lower() in ("unknown", "-"):
            continue
        facts.append(_make_fact(doc, path, value, _quote_for(lines, label_idx, value_end)))
    return facts


def _extract_property_booleans(doc: Document, lines: list[str]) -> list[Fact]:
    facts: list[Fact] = []
    bools = [
        ("Do you live at the property?",            P.PROPERTY_OWNER_OCCUPIED),
        ("Is the Property rented/leased?",          P.PROPERTY_LEASED),
        ("Is the property insured?",                P.PROPERTY_INSURED),
        ("Is the land in a bushfire prone area?",   P.PLANNING_BUSHFIRE_PRONE),
        ("Is the land affected by any planning overlays?", P.PLANNING_OVERLAYS_EXIST),
        ("Is there access to the property by road?", P.PROPERTY_ROAD_ACCESS),
        ("Do you pay land tax?",                    P.RATES_LAND_TAX_PAYABLE),
        ("Is any vacant land residential land tax payable?", P.RATES_VACANT_LAND_TAX_PAYABLE),
        ("Have any building permits been issued in the last 7 years?", P.BUILDING_PERMITS_LAST_7_YEARS),
        ("Has there been any building works/structures undertaken in the last 7 years?", P.BUILDING_WORKS_LAST_7_YEARS),
        ("Have you done any works without obtaining a permit?", P.BUILDING_WORKS_WITHOUT_PERMIT),
        ("Has any building work been done on the land that DID NOT have the required building nor planning approvals?", P.BUILDING_WORKS_WITHOUT_APPROVAL),
        ("Have you improved the property within the past 6.5 years?", P.BUILDING_IMPROVED_PAST_6_5_YEARS),
        ("Have you installed new appliances where works relating to gas, electrical and plumbing have been undertaken?", P.BUILDING_NEW_APPLIANCES_GAS_ELEC),
        ("Have you obtained the Compliance certificates - as per S.137 (c) of the Building Act", P.BUILDING_COMPLIANCE_CERTIFICATES),
        ("Does the property have a water tank?",    P.BUILDING_HAS_WATER_TANK),
        ("Was a permit required for the water tank", P.BUILDING_PERMIT_REQUIRED_WATER_TANK),
        ("Is the Property held by a Trust?",        P.VENDOR_IS_TRUST),
    ]
    for label, path in bools:
        value, label_idx, value_end = _find_value_after_label(lines, label)
        bool_value = _yes_no_to_bool(value)
        if bool_value is None:
            continue
        facts.append(_make_fact(doc, path, bool_value, _quote_for(lines, label_idx, value_end)))
    return facts


def _extract_rates(doc: Document, lines: list[str]) -> list[Fact]:
    facts: list[Fact] = []
    rate_labels = [
        ("Council",         P.RATES_COUNCIL_AUTHORITY),
        ("Council rates",   P.RATES_COUNCIL_ANNUAL),
        ("Water Authority", P.RATES_WATER_AUTHORITY),
        ("Water rates",     P.RATES_WATER_ANNUAL),
    ]
    for label, path in rate_labels:
        value, label_idx, value_end = _find_value_after_label(lines, label)
        if value is None:
            continue
        facts.append(_make_fact(doc, path, value, _quote_for(lines, label_idx, value_end)))
    return facts


def _extract_services(doc: Document, lines: list[str]) -> list[Fact]:
    """Extract one Fact per service from the connected-services checklist.

    The form lists services that ARE connected. Anything not listed below
    the header before the next section is treated as NOT connected.
    """
    facts: list[Fact] = []

    # Find the section header
    header_idx = -1
    for i, line in enumerate(lines):
        if _SERVICES_HEADER_PATTERN.search(line):
            header_idx = i
            break
    if header_idx < 0:
        return facts

    # Walk forward to collect every line until we hit a question or another section.
    end_idx = header_idx + 1
    for j in range(header_idx + 1, min(header_idx + 30, len(lines))):
        line = lines[j].strip()
        if line.endswith("?"):
            break
        if line in KNOWN_LABELS:
            break
        end_idx = j

    listed = [lines[k].strip() for k in range(header_idx + 1, end_idx + 1) if lines[k].strip()]
    listed_lower = [item.lower() for item in listed]
    snippet = "\n".join([lines[header_idx], *listed])

    for service_name, display, variants in _SERVICE_VARIANTS:
        connected = False
        matched_text = None
        for item, item_lower in zip(listed, listed_lower):
            if any(v in item_lower for v in variants):
                matched_text = item
                if any(pattern in item_lower for pattern in _NEGATIVE_SERVICE_PATTERNS):
                    connected = False
                else:
                    connected = True
                break

        path = P.service_connected(service_name)
        if connected:
            quote = f"{lines[header_idx].strip()}\n{matched_text}"
            facts.append(
                _make_fact(
                    doc, path, True, quote,
                    confidence=0.92,
                    notes=f"matched '{matched_text}' against '{display}'",
                )
            )
        else:
            notes = f"'{display}' not listed under services-connected header"
            if matched_text is not None:
                notes = f"matched '{matched_text}' but it explicitly says the service is not connected"
            facts.append(
                _make_fact(
                    doc, path, False, snippet,
                    confidence=0.85,
                    notes=notes,
                )
            )

    return facts


def _extract_insurance_disclosure(doc: Document, lines: list[str]) -> list[Fact]:
    """Capture the multi-line insurance particulars block as a single text fact.

    Authoritative insurance details (policy number, expiry, sums) live in
    the Certificate of Currency PDF — that extractor will write its own
    high-confidence facts. This vendor-form fact is a low-rank fallback.
    """
    label = "Please provide full particulars of your insurance cover:"
    target_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == label:
            target_idx = i
            break
    if target_idx < 0:
        return []

    # Read until next known label or empty separator
    captured: list[str] = []
    for j in range(target_idx + 1, min(target_idx + 12, len(lines))):
        line = lines[j].strip()
        if not line:
            if captured:
                break
            continue
        if line in KNOWN_LABELS:
            break
        captured.append(line)

    if not captured:
        return []

    quote = "\n".join([label, *captured])
    return [
        _make_fact(
            doc,
            P.INSURANCE_HOME_DISCLOSED_TEXT,
            "\n".join(captured),
            quote,
            confidence=0.80,
            notes="vendor-disclosed insurance particulars (Certificate of Currency is authoritative)",
        )
    ]


def _extract_s27(doc: Document, lines: list[str]) -> list[Fact]:
    """Extract Section 27 early release of deposit answer.

    The vendor form asks:
      "Section 27 - Do you want/need an early release of deposit?"
    Followed by a Yes or No answer (multi-line — the No option starts with 'No').
    """
    label_variants = [
        "Section 27 - Do you want/need an early release of deposit?",
        "Section 27 - Do you require an early release of deposit?",
        "Do you want/need an early release of deposit?",
    ]
    for label in label_variants:
        value, label_idx, value_end = _find_value_after_label(
            lines, label, max_value_lines=1
        )
        if value is None:
            continue
        # The "No" option is a long paragraph; "Yes" is typically just "Yes"
        bv = _yes_no_to_bool(value)
        if bv is None:
            continue
        return [
            _make_fact(
                doc, P.SALE_S27_EARLY_RELEASE, bv,
                _quote_for(lines, label_idx, value_end),
                notes="True = vendor wants s27 early deposit release",
            )
        ]
    return []


def _extract_owners_corp(doc: Document, lines: list[str]) -> list[Fact]:
    """Owners corporation question — wording is awkward in this form."""
    label_variants = [
        "If your property affected by an Owners Corporation otherwise known as Strata Insurance for common property.",
        "If your property aﬀected by an Owners Corporation otherwise known as Strata Insurance for common property.",
    ]
    for label in label_variants:
        value, label_idx, value_end = _find_value_after_label(lines, label)
        bv = _yes_no_to_bool(value)
        if bv is not None:
            return [
                _make_fact(
                    doc, P.RATES_OWNERS_CORPORATION, bv,
                    _quote_for(lines, label_idx, value_end),
                )
            ]
    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_vendor_form_facts(doc: Document) -> list[Fact]:
    """Extract every supported field from a vendor instruction form.

    Pure function — no I/O, no FactStore mutation. Caller decides where
    the resulting Facts go.

    Returns an empty list when the document is not a vendor form.
    """
    text = _doc_text(doc)
    if not text:
        return []
    if not _SERVICES_HEADER_PATTERN.search(text):
        # Conservative guard — every real vendor form has this header.
        return []

    lines = _doc_lines(doc)

    facts: list[Fact] = []
    facts.extend(_extract_vendors(doc, lines))
    facts.extend(_extract_property(doc, lines))
    facts.extend(_extract_property_booleans(doc, lines))
    facts.extend(_extract_rates(doc, lines))
    facts.extend(_extract_services(doc, lines))
    facts.extend(_extract_insurance_disclosure(doc, lines))
    facts.extend(_extract_owners_corp(doc, lines))
    facts.extend(_extract_s27(doc, lines))
    return facts
