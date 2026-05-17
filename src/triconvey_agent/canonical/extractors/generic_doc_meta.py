"""Generic document metadata extractor.

Classifies every document by its filename, then extracts:
  - Issue / search / assembled date (DD/MM/YYYY)
  - Plan numbers (PS/PC/LP/TP/AL/RP) with their dates

Emits:
  - docs.<type>.date     — date fact for each recognised doc type
  - docs.plan_of_subdivision / docs.plan_of_consolidation — list[dict]

This extractor intentionally has NO guard — it runs on every document and
falls through silently when nothing useful is found.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.schemas.documents import Document

EXTRACTOR_NAME = "rule:generic_doc_meta_v1"

# ---------------------------------------------------------------------------
# Filename → doc-type classifier
# Priority-ordered: first matching entry wins.
# ---------------------------------------------------------------------------

_CLASSIFIER: list[tuple[list[str], str]] = [
    # VicRoads
    (["vicroads", "vic_ enquiry - vicroads", "roads property certificate"], "vicroads_cert"),
    # EPA
    (["environment protection authority", "priority sites register", "epa"], "epa_cert"),
    # Detailed Property Report (not VicPlan)
    (["detailed-property-report", "detailed property report"], "property_report"),
    # VicPlan Planning Report — handled by planning_certificate extractor, but grab date here too
    (["vicplan", "planning-property-report", "planning property report"], "planning_cert"),
    # Owners Corporation
    (["owners corporation certificate"], "oc_cert"),
    (["owners corporation basic report", "oc basic report"], "oc_basic_report"),
    # Residential tenancy / lease agreements
    (["residential tenancy agreement", "residential rental agreement", "rental agreement"], "residential_tenancy_agreement"),
    # Owner Builder
    (["owner builder report"], "owner_builder_report"),
    # Building Warranty Insurance / Domestic Building Insurance
    (["bldg warranty", "building warranty", "warranty insurance", "domestic building insurance"], "bldg_warranty"),
    # Building Permit (occupancy permit also classified here)
    (["building approval", "building approval certificate"], "council_building_approval_cert"),
    (["buildingpermit", "building permit"], "building_permit"),
    (["occupancy permit", "_op."], "occupancy_permit"),
    # Copy of Plan — contains plan number in filename
    (["copy of plan", "plan of subdivision", "plan of consolidation"], "plan_doc"),
    # Instrument documents
    (["electronic instrument"], "instrument"),
    (["instrument search"], "instrument"),
    # Council certificates
    (["council land information", "land information certificate"], "council_land_info_cert"),
    # Insurance
    (["racv", "insurance policy"], "insurance_doc"),
    # Water — primary date from water_authority_certificate_v2; this is a fallback
    (["water information statement", "water encumbrance", "enquiry - yarra valley water",
      "enquiry - south east water", "enquiry - barwon water",
      "enquiry - greater western water", "enquiry - greater west water",
      "enquiry - city west water", "enquiry - south east water",
      "enquiry - coliban water", "enquiry - barwon water",
      "enquiry - westernport water", "greater western water",
      "greater west water"], "water_cert"),
    # SRO land tax — handled by land_tax extractor; date fallback here
    (["land tax certificate", "state revenue office"], "land_tax_cert"),
    # VIC Title — handled by vic_title extractor; date fallback here
    (["register search statement", "copy of title"], "vic_title"),
    # Vendor form
    (["vendor instruction form", "vendor form"], "vendor_form"),
]

# doc_type → canonical date path (None = not tracked separately)
_TYPE_TO_DATE_PATH: dict[str, str | None] = {
    "vicroads_cert":    P.DOCS_VICROADS_CERT_DATE,
    "epa_cert":         P.DOCS_EPA_CERT_DATE,
    "property_report":  P.DOCS_PROPERTY_REPORT_DATE,
    "planning_cert":    P.DOCS_PLANNING_CERT_DATE,
    "oc_cert":          P.DOCS_OC_CERT_DATE,
    "oc_basic_report":  P.DOCS_OC_BASIC_REPORT_DATE,
    "residential_tenancy_agreement": P.DOCS_RESIDENTIAL_TENANCY_AGREEMENT_DATE,
    "owner_builder_report": P.DOCS_OWNER_BUILDER_REPORT_DATE,
    "bldg_warranty":    P.DOCS_BLDG_WARRANTY_DATE,
    "building_permit":  None,
    "occupancy_permit": None,
    "plan_doc":         None,   # handled via plan number extraction
    "instrument":       None,
    "council_land_info_cert": P.DOCS_COUNCIL_LAND_INFO_CERT_DATE,
    "council_building_approval_cert": P.DOCS_COUNCIL_BUILDING_APPROVAL_CERT_DATE,
    "insurance_doc":    None,
    # Water: v2 extractor is primary (conf 0.92); generic fallback at 0.75
    "water_cert":       P.DOCS_WATER_CERT_DATE,
    "land_tax_cert":    None,
    "vic_title":        P.DOCS_VIC_TITLE_SEARCH_DATE,  # vic_title extractor uses title.produced_at
    "vendor_form":      None,
}

# ---------------------------------------------------------------------------
# Plan number classification
# ---------------------------------------------------------------------------

# Plan type prefix → "Plan of Subdivision" / "Plan of Consolidation" etc.
_PLAN_PREFIX_LABEL: dict[str, str] = {
    "PS": "Plan of Subdivision",
    "PC": "Plan of Consolidation",
    "LP": "Licensed Plan",
    "TP": "Town Planning",
    "RP": "Registered Plan",
    "SP": "Strata Plan",
    "AL": "Allocation Plan",
}

_PLAN_NUMBER_RE = re.compile(
    r"\b(PS|PC|LP|TP|RP|SP|AL)\s?(\d{4,8}[A-Z]?)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Date extraction — ordered patterns, most specific first
# ---------------------------------------------------------------------------

# DD/MM/YYYY (most common in LANDATA outputs)
_DATE_DDMMYYYY_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_DATE_DDMMYYYY_SPACED_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})\b")
_DATE_DD_MON_YYYY_RE = re.compile(
    r"\b(\d{1,2})[-\s]+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[-\s]+(\d{4})\b",
    re.IGNORECASE,
)

# Explicit labeled dates that should outrank generic billing periods and legacy references.
_DATE_ISSUE_DDMMYYYY_RE = re.compile(r"\bIssue date:?\s*(\d{2}/\d{2}/\d{4})\b", re.IGNORECASE)
_DATE_PRODUCED_DDMMYYYY_RE = re.compile(r"\bProduced:?\s*(\d{2}/\d{2}/\d{4})\b", re.IGNORECASE)
_DATE_ISSUE_WORD_OR_DASH_RE = re.compile(
    r"\bIssue date:?\s*(\d{1,2})[-/\s]+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[-/\s]+(\d{4})\b",
    re.IGNORECASE,
)
_DATE_LABELLED_DD_MON_YYYY_RE = re.compile(
    r"\b(?:DATE|Date)\s*:?\s*(\d{1,2})[-/\s]+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[-/\s]+(\d{4})\b",
    re.IGNORECASE,
)

# D/DD Month YYYY (e.g. "2 April 2026", "16 March 2026")
_DATE_DMY_WORD_RE = re.compile(
    r"\b(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+(\d{4})\b",
    re.IGNORECASE,
)

# Ordinal dates — "2nd April 2026", "2th April 2026" (VicRoads typo)
_DATE_ORDINAL_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+(\d{4})\b",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "sept": "09", "oct": "10", "nov": "11", "dec": "12",
}

# Owner Builder / report-specific labeled dates.
# Covers "DATE OF REPORT: 9th April 2026", "DATE OF INSPECTION: 9th April 2026",
# "Report Date:", "Assessment Date:", "Inspection Date:", etc.
_MONTH_NAMES = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_DATE_REPORT_LABELED_RE = re.compile(
    r"(?:DATE\s+OF\s+REPORT|DATE\s+OF\s+INSPECTION|Report\s+Date"
    r"|Generated(?:\s+on)?|Date\s+of\s+Assessment|Certificate\s+Issued"
    r"|Date\s+of\s+Issue|Period\s+of\s+Cover\s+(?:from|start)"
    r"|Assessment\s+Date|Inspection\s+Date|Application\s+(?:Received|Date))\s*[:\-]?\s*"
    r"(\d{1,2}(?:st|nd|rd|th)?[\s\-/]+" + _MONTH_NAMES + r"[\s\-/]+\d{4}"
    r"|\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
    re.IGNORECASE,
)

# "Created at 16 March 2026 05:02 PM" (Detailed Property Report)
_DATE_CREATED_AT_RE = re.compile(
    r"Created at\s+(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)

# "Document Assembled: 02/04/2026" (Copy of Plan)
_DATE_ASSEMBLED_RE = re.compile(
    r"Document Assembled:?\s*(\d{2}/\d{2}/\d{4})"
)

_INSTRUMENT_NUMBER_RE = re.compile(
    r"Document Identification\s+Number of Pages.*?\n\s*(\d{5,})\s*\n",
    re.IGNORECASE | re.DOTALL,
)

# Date of issue: "2th April 2026" — VicRoads specific
_DATE_OF_ISSUE_RE = re.compile(
    r"Date of issue:?\s+(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)

# DATE OF SEARCH: "2nd April 2026" — EPA specific
_DATE_OF_SEARCH_RE = re.compile(
    r"DATE OF SEARCH:?\s+(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)

_DATE_REPORT_RE = re.compile(
    r"\bREPORT DATE\s+(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)

_DATE_LABELLED_WORD_RE = re.compile(
    r"\b(?:DATE|Date of Issue)\s*:?\s*(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)

# "Produced DD/MM/YYYY" — VIC title
_DATE_PRODUCED_RE = re.compile(r"Produced\s+(\d{2}/\d{2}/\d{4})")
_DATE_OF_AGREEMENT_RE = re.compile(
    r"Date of agreement.*?(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})",
    re.IGNORECASE | re.DOTALL,
)

# YYYYMMDD in filename — RACV insurance: "RACV_HOM798171110_2026022216180718"
_FILENAME_DATE_RE = re.compile(r"_(\d{4})(\d{2})(\d{2})\d+")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc_text(doc: Document) -> str:
    return doc.raw_text or doc.normalized_text or ""


def _classify(filename: str, text: str = "") -> str | None:
    fn_lower = filename.lower()
    for keywords, doc_type in _CLASSIFIER:
        if any(kw in fn_lower for kw in keywords):
            return doc_type

    text_upper = text.upper()
    if "EPA PRIORITY SITES REGISTER" in text_upper:
        return "epa_cert"
    if "OWNERS CORPORATION SEARCH REPORT" in text_upper:
        return "oc_basic_report"
    if "RESIDENTIAL RENTAL AGREEMENT" in text_upper or "RESIDENTIAL TENANCY AGREEMENT" in text_upper:
        return "residential_tenancy_agreement"
    if "LAND INFORMATION CERTIFICATE" in text_upper:
        return "council_land_info_cert"
    if "BUILDING INFORMATION CERTIFICATE" in text_upper:
        return "council_building_approval_cert"
    if "ROADS PROPERTY CERTIFICATE" in text_upper:
        return "vicroads_cert"
    if "DETAILED PROPERTY REPORT" in text_upper:
        return "property_report"
    return None


def _month_word_to_num(month: str) -> str:
    return _MONTH_MAP.get(month.lower(), "01")


def _extract_date(text: str, filename: str) -> str | None:
    """Try every date pattern in priority order; return DD/MM/YYYY string or None."""
    fn_lower = filename.lower()

    # Priority 1 — document-type-specific anchored patterns
    m = _DATE_ISSUE_DDMMYYYY_RE.search(text)
    if m:
        return m.group(1)

    # Priority 1a — report/inspection labeled dates (Owner Builder, assessment reports).
    # Runs early so "DATE OF REPORT: 9th April 2026" beats generic bare-date patterns.
    m = _DATE_REPORT_LABELED_RE.search(text)
    if m:
        raw = m.group(1).strip()
        # Numeric: DD/MM/YYYY or DD-MM-YYYY
        nm = re.match(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", raw)
        if nm:
            d, mo, y = int(nm.group(1)), int(nm.group(2)), int(nm.group(3))
            y = y + 2000 if y < 100 else y
            if 2020 <= y <= 2035:
                return f"{d:02d}/{mo:02d}/{y}"
        # Word date: strip optional ordinal suffix ("9th" → "9"), then parse
        wm = re.match(
            r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/]+([A-Za-z]{3,9})[\s\-/]+(\d{4})",
            raw, re.IGNORECASE,
        )
        if wm:
            d_s, mon_s, y_s = wm.group(1), wm.group(2), wm.group(3)
            mon_num = _MONTH_MAP.get(mon_s.lower())
            if mon_num and 2020 <= int(y_s) <= 2035:
                return f"{int(d_s):02d}/{mon_num}/{y_s}"

    m = _DATE_OF_AGREEMENT_RE.search(text)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        return f"{int(day):02d}/{int(month):02d}/{year}"

    m = _DATE_PRODUCED_DDMMYYYY_RE.search(text)
    if m:
        return m.group(1)

    m = _DATE_ISSUE_WORD_OR_DASH_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    m = _DATE_LABELLED_DD_MON_YYYY_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    m = _DATE_DD_MON_YYYY_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    m = _DATE_REPORT_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    m = _DATE_LABELLED_WORD_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    if "date of issue" in fn_lower or "date of issue" in text.lower():
        m = _DATE_OF_ISSUE_RE.search(text)
        if m:
            d, mon, y = m.group(1), m.group(2), m.group(3)
            return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    if "date of search" in text.upper():
        m = _DATE_OF_SEARCH_RE.search(text)
        if m:
            d, mon, y = m.group(1), m.group(2), m.group(3)
            return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    # Priority 2 — "Created at DD Month YYYY"
    m = _DATE_CREATED_AT_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    # Priority 3 — "Document Assembled: DD/MM/YYYY"
    m = _DATE_ASSEMBLED_RE.search(text)
    if m:
        return m.group(1)

    # Priority 4 — "Produced DD/MM/YYYY"
    m = _DATE_PRODUCED_RE.search(text)
    if m:
        return m.group(1)

    # Priority 5 — ordinal word dates ("2nd April 2026")
    m = _DATE_ORDINAL_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    # Priority 6 — plain word dates ("16 March 2026")
    m = _DATE_DMY_WORD_RE.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}/{_month_word_to_num(mon)}/{y}"

    # Priority 7 — bare DD/MM/YYYY (first occurrence, skip really old dates)
    for m in _DATE_DDMMYYYY_RE.finditer(text):
        date_str = m.group(1)
        year = int(date_str.split("/")[2])
        if 2020 <= year <= 2035:
            return date_str

    m = _DATE_DDMMYYYY_SPACED_RE.search(text)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        if 2020 <= int(year) <= 2035:
            return f"{int(day):02d}/{int(month):02d}/{year}"

    # Priority 8 — YYYYMMDD in filename (RACV style)
    m = _FILENAME_DATE_RE.search(filename)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{d}/{mo}/{y}"

    return None


def _extract_plan_numbers(text: str, filename: str) -> list[dict]:
    """Return list of {prefix, number, full, label} dicts for every plan reference."""
    plans = []
    seen: set[str] = set()
    for m in _PLAN_NUMBER_RE.finditer(text + " " + filename.upper()):
        prefix = m.group(1).upper()
        num = m.group(2).upper()
        full = f"{prefix}{num}"
        if full not in seen:
            seen.add(full)
            plans.append({
                "prefix": prefix,
                "number": num,
                "full": full,
                "label": _PLAN_PREFIX_LABEL.get(prefix, "Plan"),
            })
    return plans


def _make_source(doc: Document, quote: str) -> Source:
    return Source(file=doc.filename, page=None, quote=quote, quote_verified=True)


def _make_fact(
    doc: Document,
    path: str,
    value: object,
    quote: str,
    *,
    confidence: float = 0.90,
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
# Public entry point
# ---------------------------------------------------------------------------

def extract_generic_doc_meta_facts(doc: Document) -> list[Fact]:
    """Classify document by filename, extract date and plan numbers.

    Always runs — silently returns [] when nothing is found.
    """
    filename = Path(doc.filename).name
    text = _doc_text(doc)
    doc_type = _classify(filename, text)
    facts: list[Fact] = []

    # ── Date extraction ──────────────────────────────────────────────────────
    date_str = _extract_date(text, filename)

    if doc_type is not None:
        date_path = _TYPE_TO_DATE_PATH.get(doc_type)
        if date_path and date_str:
            # Water cert date: dedicated extractor runs at 0.92; this is a fallback only
            conf = 0.72 if doc_type == "water_cert" else 0.90
            facts.append(
                _make_fact(
                    doc, date_path, date_str, f"date={date_str} from {filename}",
                    confidence=conf,
                    notes=f"auto-extracted from {doc_type} document",
                )
            )

    # ── Plan number extraction ───────────────────────────────────────────────
    # Only copy-of-plan docs and titles should contribute plan attachments.
    if doc_type in ("plan_doc", "vic_title"):
        plans = _extract_plan_numbers(text, filename)
        for plan in plans:
            prefix = plan["prefix"]
            full = plan["full"]
            if prefix in ("PS", "LP", "TP"):
                # PS = Plan of Subdivision (modern)
                # LP = Licensed Plan (pre-PS Victorian lot, shown as "Plan of Subdivision LP...")
                # TP = Title Plan (old Crown allotment, same display treatment)
                facts.append(
                    _make_fact(
                        doc, P.DOCS_PLAN_OF_SUBDIVISION,
                        json.dumps({"number": full, "date": date_str or "##"}),
                        f"plan number {full} date {date_str}",
                        confidence=0.95,
                        notes=f"Plan reference ({prefix}) from {filename}",
                    )
                )
            elif prefix in ("PC",):
                facts.append(
                    _make_fact(
                        doc, P.DOCS_PLAN_OF_CONSOLIDATION,
                        json.dumps({"number": full, "date": date_str or "##"}),
                        f"plan number {full} date {date_str}",
                        confidence=0.95,
                        notes=f"Plan of Consolidation from {filename}",
                    )
                )

    if doc_type == "instrument" and "covenant" in filename.lower():
        number_match = _INSTRUMENT_NUMBER_RE.search(text)
        if number_match and date_str:
            facts.append(
                _make_fact(
                    doc,
                    P.DOCS_COVENANT,
                    json.dumps({"number": number_match.group(1), "date": date_str}),
                    f"covenant instrument {number_match.group(1)} date {date_str}",
                    confidence=0.95,
                    notes=f"Covenant instrument from {filename}",
                )
            )

    return facts
