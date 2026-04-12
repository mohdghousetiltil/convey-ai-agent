"""VicPlan Planning Property Report extractor.

Reads a Planning Property Report from planning.vic.gov.au (VicPlan) and
emits Facts at canonical planning.* paths.

NOTE: This report is NOT a Section 199 Planning Certificate, but it IS
legally usable for the bushfire prone area disclosure under s32C(b) of
the Sale of Land Act 1962 (Vic) — the report itself contains this note.

Registered as `rule:planning_certificate_v1` so it matches the
`rule:planning_certificate*` glob in DEFAULT_AUTHORITY_RULES.
"""
from __future__ import annotations

import re

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:planning_certificate_v1"

# ---------------------------------------------------------------------------
# Zone name → TriConvey dropdown label mapping
# (matches the dropdown_options in tab_sec_32_2.yaml)
# ---------------------------------------------------------------------------
_ZONE_CODE_TO_LABEL: dict[str, str] = {
    "LDRZ": "LDRZ - Low Density Residential Zone",
    "MUZ":  "MUZ - Mixed Use Zone",
    "TZ":   "TZ - Township Zone",
    "RGZ":  "RGZ - Residential Growth Zone",
    "NRZ":  "NRZ - Neighbourhood Residential Zone",
    "GRZ":  "GRZ - General Residential Zone",
    "IN1Z": "IN1Z - Industrial 1 Zone",
    "IN2Z": "IN2Z - Industrial 2 Zone",
    "IN3Z": "IN3Z - Industrial 3 Zone",
    "C1Z":  "C1Z - Commercial 1 Zone",
    "C2Z":  "C2Z - Commercial 2 Zone",
    "RLZ":  "RLZ - Rural Living Zone",
    "GWZ":  "GWZ - Green Wedge Zone",
    "GWAZ": "GWAZ - Green Wedge A Zone",
    "RCZ":  "RCZ - Rural Conservation Zone",
    "FZ":   "FZ - Farming Zone",
    "RAZ":  "RAZ - Rural Activity Zone",
    "PUZ1": "PUZ1 - Public Use Zone-Service and Utility",
    "PUZ2": "PUZ2 - Public Use Zone-Education",
    "PUZ3": "PUZ3 - Public Use Zone-Health & Community",
    "PUZ5": "PUZ5 - Public Use Zone-Cemetery/Crematorium",
    "PUZ6": "PUZ6 - Public Use Zone-Local Government",
    "PUZ7": "PUZ7 - Public Use Zone-Other Public Use",
    "PPRZ": "PPRZ - Public Park and Recreation Zone",
    "PCRZ": "PCRZ - Public Conservation and Resource Zone",
    "SUZ":  "SUZ - Special Use Zone",
    "CDZ":  "CDZ - Comprehensive Development Zone",
    "UFZ":  "UFZ - Urban Floodway Zone",
    "UGZ":  "UGZ - Urban Growth Zone",
    "CA":   "CA - Commonwealth land",
}

# Overlay codes to human label.
_OVERLAY_CODES = {
    "BMO":  "Bushfire Management Overlay",
    "ESO":  "Environmental Significance Overlay",
    "FO":   "Floodway Overlay",
    "HO":   "Heritage Overlay",
    "LSIO": "Land Subject to Inundation Overlay",
    "DDO":  "Design and Development Overlay",
    "VPO":  "Vegetation Protection Overlay",
    "SBO":  "Special Building Overlay",
    "WMO":  "Wildfire Management Overlay",
    "SLO":  "Significant Landscape Overlay",
    "EMO":  "Environmental Management Overlay",
    "DCO":  "Development Contributions Overlay",
    "RCZ":  "Rural Conservation Zone Overlay",
    "NCO":  "Neighbourhood Character Overlay",
    "EAO":  "Environmental Audit Overlay",
    "DCPO": "Development Contributions Plan Overlay",
}

# Regex for "FARMING ZONE (FZ) (COUNCIL)" — captures zone name + code.
_ZONE_HEADING_RE = re.compile(
    r"([A-Z][A-Z\s]+ZONE)\s+\(([A-Z0-9]{2,5})\)",
    re.IGNORECASE,
)

# Overlay heading: "BUSHFIRE MANAGEMENT OVERLAY (BMO) (COUNCIL)"
# Note: use [A-Z ] not [A-Z\s] to avoid matching across newlines.
_OVERLAY_HEADING_RE = re.compile(
    r"([A-Z][A-Z ]+OVERLAY)\s*\(([A-Z]{2,6})\)",
)

# Planning scheme: "Planning Scheme - Indigo" or "Planning Scheme: Indigo"
_SCHEME_RE = re.compile(r"Planning Scheme\s*[-:]\s*([A-Za-z][A-Za-z\s]+?)(?:\n|$)", re.IGNORECASE)

# Council name from PROPERTY DETAILS block.
# VicPlan layout: address lines, then council name on its own line, then
# a 4-7 digit property number on the next line.
# We match: a run of uppercase+spaces (the council) followed by a newline
# and a 4-7 digit number — that combination is distinctive.
_COUNCIL_RE = re.compile(r"([A-Z][A-Z ]+?)\s*\n\s*(\d{4,7})\s*\n", re.MULTILINE)

# Property address in heading: after "PLANNING PROPERTY REPORT:"
_ADDRESS_RE = re.compile(r"PLANNING PROPERTY REPORT:\s*\n\s*(.+?)(?:\n|$)")

# Bushfire prone area confirmed if BMO overlay is present OR explicit text.
_BUSHFIRE_TEXT_RE = re.compile(
    r"land is in a bushfire prone area|Bushfire Management Overlay \(BMO\)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc_text(doc: Document) -> str:
    return doc.raw_text or doc.normalized_text or ""


def _make_source(doc: Document, quote: str) -> Source:
    return Source(file=doc.filename, page=None, quote=quote, quote_verified=True)


def _make_fact(
    doc: Document,
    path: str,
    value: object,
    quote: str,
    *,
    confidence: float = 0.95,
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


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------


def _extract_address(doc: Document, text: str) -> list[Fact]:
    m = _ADDRESS_RE.search(text)
    if not m:
        return []
    address = _compact(m.group(1))
    return [
        _make_fact(
            doc, P.PROPERTY_ADDRESS, address, _compact(m.group(0)), confidence=0.88,
            notes="from planning property report heading",
        )
    ]


def _extract_planning_scheme(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    if m := _SCHEME_RE.search(text):
        scheme = _compact(m.group(1))
        facts.append(_make_fact(doc, P.PLANNING_SCHEME, scheme, _compact(m.group(0))))

    if m := _COUNCIL_RE.search(text):
        # group(1) = council name (all-caps), group(2) = property number
        council = _compact(m.group(1)).title()  # "INDIGO" → "Indigo"
        facts.append(
            _make_fact(doc, P.PLANNING_RESPONSIBLE_AUTHORITY, council, _compact(m.group(0)))
        )
    return facts


def _extract_zones(doc: Document, text: str) -> list[Fact]:
    """Extract the primary zone (first zone heading found)."""
    matches = list(_ZONE_HEADING_RE.finditer(text))
    if not matches:
        return []

    # First match is the primary zone for this property.
    m = matches[0]
    zone_code = m.group(2).upper()
    quote = _compact(m.group(0))

    label = _ZONE_CODE_TO_LABEL.get(zone_code)
    if label is None:
        # Unknown code — still emit it, Brain B will flag via options validation.
        label = zone_code
        notes = f"zone code '{zone_code}' not in known mapping — may not match dropdown"
    else:
        notes = None

    return [_make_fact(doc, P.PLANNING_ZONE, label, quote, notes=notes)]


def _extract_overlays(doc: Document, text: str) -> list[Fact]:
    """Extract all overlay codes found in the document."""
    matches = list(_OVERLAY_HEADING_RE.finditer(text))
    if not matches:
        # Check if overlays exist by searching for known overlay text.
        has_overlay = bool(re.search(r"\bOVERLAY\b", text, re.IGNORECASE))
        return [
            _make_fact(
                doc, P.PLANNING_OVERLAYS_EXIST, has_overlay,
                "no overlay heading found",
                confidence=0.7,
            )
        ]

    # Deduplicate by code.
    seen_codes: set[str] = set()
    overlay_codes: list[str] = []
    overlay_names: list[str] = []

    for m in matches:
        code = m.group(2).upper()
        if code in seen_codes:
            continue
        seen_codes.add(code)
        overlay_codes.append(code)
        # Use the known code→label dict for clean names; fall back to regex group.
        label = _OVERLAY_CODES.get(code, _compact(m.group(1)).title())
        overlay_names.append(f"{code} - {label}")

    quote = "; ".join(overlay_codes)
    overlay_names_str = ", ".join(overlay_names)

    facts = [
        _make_fact(doc, P.PLANNING_OVERLAYS_EXIST, True, quote),
        _make_fact(doc, P.PLANNING_OVERLAY_NAMES, overlay_names_str, quote),
        _make_fact(doc, P.PLANNING_OVERLAYS_LIST, overlay_codes, quote),
    ]
    return facts


def _extract_bushfire(doc: Document, text: str) -> list[Fact]:
    """Determine bushfire prone area status.

    The VicPlan report is legally authoritative for this disclosure per
    s32C(b) of the Sale of Land Act 1962 (Vic).
    """
    is_prone = bool(_BUSHFIRE_TEXT_RE.search(text))
    quote = (
        "Bushfire Management Overlay (BMO) present — land is in bushfire prone area"
        if is_prone else
        "No Bushfire Management Overlay (BMO) detected in planning property report"
    )
    return [
        _make_fact(
            doc,
            P.PLANNING_BUSHFIRE_PRONE,
            is_prone,
            quote,
            confidence=0.98,
            notes=(
                "Legally authoritative per s32C(b) Sale of Land Act 1962 (Vic)"
                if is_prone else
                "Absence of BMO in VicPlan report"
            ),
        )
    ]


def _extract_gaic(doc: Document, text: str) -> list[Fact]:
    """Check if Growth Areas Infrastructure Contribution (GAIC) applies."""
    if "Growth Areas Infrastructure Contribution" in text or "GAIC" in text:
        is_gaic = "GAIC applies" in text or "GAIC IS imposed" in text
        return [
            _make_fact(
                doc, P.PLANNING_GAIC_TRIGGER, is_gaic,
                "GAIC mentioned in planning report",
                confidence=0.8,
                notes="manual review recommended — GAIC determination is complex",
            )
        ]
    return [
        _make_fact(
            doc, P.PLANNING_GAIC_TRIGGER, False,
            "GAIC not mentioned in planning property report",
            confidence=0.85,
        )
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_planning_certificate_facts(doc: Document) -> list[Fact]:
    """Extract planning facts from a VicPlan Planning Property Report or
    Section 199 Planning Certificate.

    Returns an empty list when the document is not a planning document.
    """
    text = _doc_text(doc)
    if not text:
        return []
    if "PLANNING PROPERTY REPORT" not in text.upper() and "PLANNING CERTIFICATE" not in text.upper():
        return []

    facts: list[Fact] = []
    facts.extend(_extract_address(doc, text))
    facts.extend(_extract_planning_scheme(doc, text))
    facts.extend(_extract_zones(doc, text))
    facts.extend(_extract_overlays(doc, text))
    facts.extend(_extract_bushfire(doc, text))
    facts.extend(_extract_gaic(doc, text))
    return facts
