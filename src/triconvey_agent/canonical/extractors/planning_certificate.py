"""VicPlan Planning Property Report extractor.

Reads a Planning Property Report from planning.vic.gov.au (VicPlan) and
emits Facts at canonical planning.* paths.

NOTE: This report is NOT a Section 199 Planning Certificate, but it IS
legally usable for the bushfire prone area disclosure under s32C(b) of
the Sale of Land Act 1962 (Vic).

Registered as `rule:planning_certificate_v1` so it matches the
`rule:planning_certificate*` glob in DEFAULT_AUTHORITY_RULES.
"""
from __future__ import annotations

import re

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:planning_certificate_v1"

_ZONE_CODE_TO_LABEL: dict[str, str] = {
    "LDRZ": "LDRZ - Low Density Residential Zone",
    "MUZ": "MUZ - Mixed Use Zone",
    "TZ": "TZ - Township Zone",
    "RGZ": "RGZ - Residential Growth Zone",
    "NRZ": "NRZ - Neighbourhood Residential Zone",
    "GRZ": "GRZ - General Residential Zone",
    "IN1Z": "IN1Z - Industrial 1 Zone",
    "IN2Z": "IN2Z - Industrial 2 Zone",
    "IN3Z": "IN3Z - Industrial 3 Zone",
    "C1Z": "C1Z - Commercial 1 Zone",
    "C2Z": "C2Z - Commercial 2 Zone",
    "RLZ": "RLZ - Rural Living Zone",
    "GWZ": "GWZ - Green Wedge Zone",
    "GWAZ": "GWAZ - Green Wedge A Zone",
    "RCZ": "RCZ - Rural Conservation Zone",
    "FZ": "FZ - Farming Zone",
    "RAZ": "RAZ - Rural Activity Zone",
    "PUZ1": "PUZ1 - Public Use Zone-Service and Utility",
    "PUZ2": "PUZ2 - Public Use Zone-Education",
    "PUZ3": "PUZ3 - Public Use Zone-Health & Community",
    "PUZ5": "PUZ5 - Public Use Zone-Cemetery/Crematorium",
    "PUZ6": "PUZ6 - Public Use Zone-Local Government",
    "PUZ7": "PUZ7 - Public Use Zone-Other Public Use",
    "PPRZ": "PPRZ - Public Park and Recreation Zone",
    "PCRZ": "PCRZ - Public Conservation and Resource Zone",
    "SUZ": "SUZ - Special Use Zone",
    "CDZ": "CDZ - Comprehensive Development Zone",
    "UFZ": "UFZ - Urban Floodway Zone",
    "UGZ": "UGZ - Urban Growth Zone",
    "CA": "CA - Commonwealth land",
}

_OVERLAY_CODES = {
    "BMO": "Bushfire Management Overlay",
    "ESO": "Environmental Significance Overlay",
    "FO": "Floodway Overlay",
    "HO": "Heritage Overlay",
    "LSIO": "Land Subject to Inundation Overlay",
    "DDO": "Design and Development Overlay",
    "VPO": "Vegetation Protection Overlay",
    "SBO": "Special Building Overlay",
    "WMO": "Wildfire Management Overlay",
    "SLO": "Significant Landscape Overlay",
    "EMO": "Environmental Management Overlay",
    "DCO": "Development Contributions Overlay",
    "RCZ": "Rural Conservation Zone Overlay",
    "NCO": "Neighbourhood Character Overlay",
    "EAO": "Environmental Audit Overlay",
    "DCPO": "Development Contributions Plan Overlay",
}

_ZONE_HEADING_RE = re.compile(r"([A-Z][A-Z\s]+ZONE)\s+\(([A-Z0-9]{2,5})\)", re.IGNORECASE)
_OVERLAY_HEADING_RE = re.compile(r"([A-Z][A-Z ]+OVERLAY)\s*\(([A-Z]{2,6})\)")
_SCHEME_RE = re.compile(r"Planning Scheme\s*[-:]\s*([A-Za-z][A-Za-z\s]+?)(?:\n|$)", re.IGNORECASE)
_COUNCIL_RE = re.compile(r"([A-Z][A-Z ]+?)\s*\n\s*(\d{4,7})\s*\n", re.MULTILINE)
_ADDRESS_RE = re.compile(r"PLANNING PROPERTY REPORT:\s*\n\s*(.+?)(?:\n|$)")

_BUSHFIRE_NEGATIVE_PATTERNS = [
    re.compile(r"(this property is not in a designated bushfire[- ]prone area[^.\n]*[.\n]?)", re.IGNORECASE),
    re.compile(r"(property is not in a designated bushfire[- ]prone area[^.\n]*[.\n]?)", re.IGNORECASE),
    re.compile(r"(not\s+(?:a\s+)?designated\s+bushfire[- ]?prone\s+area[^.\n]*[.\n]?)", re.IGNORECASE),
    re.compile(r"(is\s+not\s+(?:in\s+)?(?:a\s+)?bushfire[- ]?prone\s+area[^.\n]*[.\n]?)", re.IGNORECASE),
    re.compile(r"(no\s+bushfire\s+(?:management\s+)?overlay[^.\n]*[.\n]?)", re.IGNORECASE),
]

_BUSHFIRE_POSITIVE_PATTERNS = [
    re.compile(r"(this property is in a designated bushfire[- ]prone area[^.\n]*[.\n]?)", re.IGNORECASE),
    re.compile(r"(property is in a designated bushfire[- ]prone area[^.\n]*[.\n]?)", re.IGNORECASE),
    re.compile(r"(land is in a bushfire[- ]prone area[^.\n]*[.\n]?)", re.IGNORECASE),
    re.compile(r"(Bushfire Management Overlay\s*\(BMO\)\s+(?:present|applies)[^.\n]*[.\n]?)", re.IGNORECASE),
    re.compile(r"(BMO\s+(?:present|applies)[^.\n]*[.\n]?)", re.IGNORECASE),
]

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


def _compact(value: str) -> str:
    return " ".join(value.split())


def _clean_page_texts(doc: Document) -> list[str]:
    pages = doc.pages or []
    if not pages:
        raw = _doc_text(doc).strip()
        return [raw] if raw else []

    repeated_head: dict[str, int] = {}
    repeated_foot: dict[str, int] = {}
    for page in pages:
        lines = [line.strip() for line in (page.text or page.normalized_text or "").splitlines() if line.strip()]
        for line in lines[:3]:
            repeated_head[line] = repeated_head.get(line, 0) + 1
        for line in lines[-3:]:
            repeated_foot[line] = repeated_foot.get(line, 0) + 1

    repeated = {
        line
        for line, count in {**repeated_head, **repeated_foot}.items()
        if count >= 2 and len(line) <= 120
    }

    cleaned_pages: list[str] = []
    for page in pages:
        lines = [line.strip() for line in (page.text or page.normalized_text or "").splitlines() if line.strip()]
        filtered = [line for line in lines if line not in repeated]
        page_text = "\n".join(filtered).strip()
        if page_text:
            cleaned_pages.append(page_text)
    return cleaned_pages


def _extract_address(doc: Document, text: str) -> list[Fact]:
    match = _ADDRESS_RE.search(text)
    if not match:
        return []
    address = _compact(match.group(1))
    return [
        _make_fact(
            doc,
            P.PROPERTY_ADDRESS,
            address,
            _compact(match.group(0)),
            confidence=0.88,
            notes="from planning property report heading",
        )
    ]


def _extract_planning_scheme(doc: Document, text: str) -> list[Fact]:
    facts: list[Fact] = []
    if match := _SCHEME_RE.search(text):
        scheme = _compact(match.group(1))
        facts.append(_make_fact(doc, P.PLANNING_SCHEME, scheme, _compact(match.group(0))))

        # Derive council name from the planning scheme name (e.g. "Yarra Planning Scheme"
        # → "Yarra City Council"). Emit as a low-confidence fallback for the council
        # rates authority name — the council rates certificate extractor wins at 0.97.
        scheme_clean = re.sub(r"\s*planning\s+scheme\s*$", "", scheme, flags=re.IGNORECASE).strip()
        if scheme_clean:
            council_fallback = f"{scheme_clean} Council"
            facts.append(
                _make_fact(
                    doc, P.RATES_COUNCIL_AUTHORITY, council_fallback,
                    _compact(match.group(0)),
                    confidence=0.72,
                    notes=(
                        "Council name inferred from planning scheme name — "
                        "council rates certificate is preferred source."
                    ),
                )
            )

    if match := _COUNCIL_RE.search(text):
        council = _compact(match.group(1)).title()
        facts.append(_make_fact(doc, P.PLANNING_RESPONSIBLE_AUTHORITY, council, _compact(match.group(0))))
        # Also emit as council rates authority fallback (higher confidence than scheme-derived)
        facts.append(
            _make_fact(
                doc, P.RATES_COUNCIL_AUTHORITY, council,
                _compact(match.group(0)),
                confidence=0.80,
                notes=(
                    "Council name from planning certificate responsible authority — "
                    "council rates certificate is preferred source."
                ),
            )
        )
    return facts


def _extract_zones(doc: Document, text: str) -> list[Fact]:
    matches = list(_ZONE_HEADING_RE.finditer(text))
    if not matches:
        return []
    match = matches[0]
    zone_code = match.group(2).upper()
    label = _ZONE_CODE_TO_LABEL.get(zone_code, zone_code)
    notes = None if zone_code in _ZONE_CODE_TO_LABEL else f"zone code '{zone_code}' not in known mapping may not match dropdown"
    return [_make_fact(doc, P.PLANNING_ZONE, label, _compact(match.group(0)), notes=notes)]


def _extract_overlays(doc: Document, text: str) -> list[Fact]:
    matches = list(_OVERLAY_HEADING_RE.finditer(text))
    if not matches:
        has_overlay = bool(re.search(r"\bOVERLAY\b", text, re.IGNORECASE))
        return [_make_fact(doc, P.PLANNING_OVERLAYS_EXIST, has_overlay, "no overlay heading found", confidence=0.7)]

    seen_codes: set[str] = set()
    overlay_codes: list[str] = []
    overlay_names: list[str] = []
    for match in matches:
        code = match.group(2).upper()
        if code in seen_codes:
            continue
        seen_codes.add(code)
        overlay_codes.append(code)
        overlay_names.append(f"{code} - {_OVERLAY_CODES.get(code, _compact(match.group(1)).title())}")

    quote = "; ".join(overlay_codes)
    return [
        _make_fact(doc, P.PLANNING_OVERLAYS_EXIST, True, quote),
        _make_fact(doc, P.PLANNING_OVERLAY_NAMES, ", ".join(overlay_names), quote),
        _make_fact(doc, P.PLANNING_OVERLAYS_LIST, overlay_codes, quote),
    ]


def _extract_bushfire(doc: Document, text: str) -> list[Fact]:
    """Extract an explicit bushfire-prone answer from the planning document.

    We do not emit a synthetic False when the planning report is silent, because
    that creates false conflicts against vendor fallback or stronger planning
    statements from other pages.
    """
    search_spaces = _clean_page_texts(doc)
    if not search_spaces:
        search_spaces = [text]

    # VicPlan page cleanup often leaves the decisive sentence at the top of the
    # cleaned bushfire page. Prefer those explicit property-level statements
    # before considering broader patterns anywhere else.
    for search_text in search_spaces:
        if match := re.search(
            r"(This property is not in a designated bushfire[- ]prone area[^.\n]*[.\n]?)",
            search_text,
            re.IGNORECASE,
        ):
            return [
                _make_fact(
                    doc,
                    P.PLANNING_BUSHFIRE_PRONE,
                    False,
                    _compact(match.group(1)),
                    confidence=0.99,
                    notes="Explicit negative statement in planning property report",
                )
            ]
        if match := re.search(
            r"(This property is in a designated bushfire[- ]prone area[^.\n]*[.\n]?)",
            search_text,
            re.IGNORECASE,
        ):
            return [
                _make_fact(
                    doc,
                    P.PLANNING_BUSHFIRE_PRONE,
                    True,
                    _compact(match.group(1)),
                    confidence=0.99,
                    notes="Explicit positive statement in planning property report",
                )
            ]

    for search_text in search_spaces:

        for pattern in _BUSHFIRE_NEGATIVE_PATTERNS:
            if match := pattern.search(search_text):
                return [
                    _make_fact(
                        doc,
                        P.PLANNING_BUSHFIRE_PRONE,
                        False,
                        _compact(match.group(1)),
                        confidence=0.99,
                        notes="Explicit negative statement in planning property report",
                    )
                ]

        for pattern in _BUSHFIRE_POSITIVE_PATTERNS:
            if match := pattern.search(search_text):
                return [
                    _make_fact(
                        doc,
                        P.PLANNING_BUSHFIRE_PRONE,
                        True,
                        _compact(match.group(1)),
                        confidence=0.98,
                        notes="Legally authoritative per s32C(b) Sale of Land Act 1962 (Vic)",
                    )
                ]

    return []


def _extract_gaic(doc: Document, text: str) -> list[Fact]:
    if "Growth Areas Infrastructure Contribution" in text or "GAIC" in text:
        is_gaic = "GAIC applies" in text or "GAIC IS imposed" in text
        return [
            _make_fact(
                doc,
                P.PLANNING_GAIC_TRIGGER,
                is_gaic,
                "GAIC mentioned in planning report",
                confidence=0.8,
                notes="manual review recommended; GAIC determination is complex",
            )
        ]
    return [_make_fact(doc, P.PLANNING_GAIC_TRIGGER, False, "GAIC not mentioned in planning property report", confidence=0.85)]


def extract_planning_certificate_facts(doc: Document) -> list[Fact]:
    """Extract planning facts from a VicPlan Planning Property Report or Section 199 certificate."""
    text = _doc_text(doc)
    if not text:
        return []
    upper = text.upper()
    if "PLANNING PROPERTY REPORT" not in upper and "PLANNING CERTIFICATE" not in upper:
        return []

    facts: list[Fact] = []
    facts.extend(_extract_address(doc, text))
    facts.extend(_extract_planning_scheme(doc, text))
    facts.extend(_extract_zones(doc, text))
    facts.extend(_extract_overlays(doc, text))
    facts.extend(_extract_bushfire(doc, text))
    facts.extend(_extract_gaic(doc, text))
    return facts
