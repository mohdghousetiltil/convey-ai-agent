"""Attachment list builder for Tab 6 field 13.

Reads the FactStore and constructs the formatted attachment list.
Items whose documents are not in the bundle are flagged with ## for human review.
Items that clearly do not apply (e.g., Plan of Consolidation when there is none)
are omitted rather than flagged.

Output format (one item per line, dash-prefixed):
    - Due Diligence Checklist
    - Register Search Statement Volume 12287 Folio 279 dated 02/04/2026
    - Plan of Subdivision PS826454D dated 02/04/2026
    ...
"""
from __future__ import annotations

import json
import re

from triconvey_agent.canonical.extractors import paths as P
from triconvey_agent.canonical.schemas import Fact, Source
from triconvey_agent.normalizers.display_names import (
    normalize_council_display_name,
    normalize_water_authority_display_name,
)

EXTRACTOR_NAME = "rule:policy_attachments_v1"

_REVIEW_PLACEHOLDER = "##"
_OWNER_BUILDER_PLACEHOLDER = "***"

# Strip time component: "02/04/2026 06:20 PM" → "02/04/2026"
_DATE_STRIP_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")

# Volume/Folio patterns
_VOL_FOLIO_RE = re.compile(
    r"(?:VOLUME|Volume|VOL\.?)[\s:]*(\d+)[\s,]*(?:FOLIO|Folio)[\s:]*(\d+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_date(s: str | None) -> str:
    """Strip time from date string; return ## if not a valid date."""
    if not s or s == _REVIEW_PLACEHOLDER:
        return _REVIEW_PLACEHOLDER
    m = _DATE_STRIP_RE.search(str(s))
    return m.group(1) if m else str(s)


def _parse_vol_folio(vf: str) -> tuple[str, str]:
    """Parse vol and folio from various formats."""
    vf = str(vf).strip()
    # "12287/279"
    if "/" in vf:
        parts = vf.split("/", 1)
        return parts[0].strip(), parts[1].strip()
    # "VOLUME 12287 FOLIO 279"
    m = _VOL_FOLIO_RE.search(vf)
    if m:
        return m.group(1), m.group(2)
    # Last resort: take all digit groups
    nums = re.findall(r"\d+", vf)
    if len(nums) >= 2:
        return nums[0], nums[-1]
    return vf, ""


def _get_value(store, path: str) -> object | None:
    fact, _ = store.get(path)
    return fact.value if fact else None


def _get_all_facts(store, path: str) -> list[Fact]:
    return store.get_all(path)


def _pick_fact_value(
    store,
    path: str,
    *,
    preferred_extractors: tuple[str, ...] = (),
) -> object | None:
    facts = _get_all_facts(store, path)
    if not facts:
        return None
    for extractor in preferred_extractors:
        for fact in facts:
            if fact.extractor == extractor:
                return fact.value
    winner, _ = store.get(path)
    if winner is not None:
        return winner.value
    return facts[0].value


def _has_fact(store, path: str) -> bool:
    return bool(_get_all_facts(store, path))


def _date_from_path(store, path: str) -> str:
    val = _pick_fact_value(store, path)
    return _clean_date(str(val)) if val is not None else _REVIEW_PLACEHOLDER


def _line_with_optional_date(label: str, date: str) -> str:
    if not date or date == _REVIEW_PLACEHOLDER:
        return f"- {label}"
    return f"- {label} dated {date}"


def _strip_plan_prefix(plan_number: str, prefix: str) -> str:
    plan_number = str(plan_number).strip().upper()
    if plan_number.startswith(prefix):
        return plan_number[len(prefix):]
    return plan_number


def _normalize_plan_num_key(num: str) -> str:
    """Normalize a plan number for deduplication.

    Strips ALL known letter prefixes and leading zeros so that
    'LP121955', 'PS121955', and bare '121955' all produce '_121955'
    and are treated as the same plan. The display form is chosen by
    processing order (title-derived LP beats generic-meta PS).
    """
    s = str(num).strip().upper()
    for prefix in ("PS", "PC", "LP", "TP", "RP", "SP", "AL"):
        if s.startswith(prefix):
            rest = s[len(prefix):]
            # Strip trailing alpha suffix for the key (e.g. PS826454D → 826454)
            digits = re.sub(r"[^0-9]", "", rest)
            try:
                return f"_{int(digits)}"
            except ValueError:
                return s
    try:
        return f"_{int(re.sub(r'[^0-9]', '', s))}"
    except ValueError:
        return s


def _source_file(fact: Fact) -> str:
    return fact.sources[0].file if fact.sources else ""


def _title_dates_by_file(store) -> dict[str, str]:
    file_to_date: dict[str, str] = {}
    for fact in _get_all_facts(store, P.TITLE_PRODUCED_AT) + _get_all_facts(store, P.DOCS_VIC_TITLE_SEARCH_DATE):
        src_file = _source_file(fact)
        if src_file:
            file_to_date[src_file] = _clean_date(str(fact.value))
    return file_to_date


def _normalise_council_name(name: object | None) -> str | None:
    """Return a display-ready council name.

    Applies known corrections and ensures the word "Council" appears somewhere
    in the name (solicitor convention: never just "Merri-bek", always
    "Merri-bek Council").
    """
    if name is None:
        return None
    return normalize_council_display_name(str(name).strip())


def _normalise_water_name(name: object | None) -> str | None:
    if name is None:
        return None
    value = normalize_water_authority_display_name(str(name).strip())
    return value.strip() if value else None


def _norm_vol(vol: str) -> str:
    """Normalise a volume number for deduplication: strip leading zeros.

    "07133" and "7133" both normalise to "7133" so they deduplicate.
    The display string keeps the raw value as extracted.
    """
    try:
        return str(int(vol.strip()))
    except (ValueError, AttributeError):
        return str(vol).strip()


def _title_entries(store) -> list[str]:
    """Build one 'Register Search Statement' line per title document.

    Deduplication uses normalised (integer) volume+folio so that
    zero-padded variants ('07133' vs '7133') are treated as the same title.
    """
    vf_facts = _get_all_facts(store, "title.volume_folio")
    date_facts = _get_all_facts(store, "title.produced_at")
    search_date_facts = _get_all_facts(store, P.DOCS_VIC_TITLE_SEARCH_DATE)

    # Build file → date mapping (produced_at or generic search date)
    file_to_date: dict[str, str] = {}
    for f in date_facts + search_date_facts:
        if f.sources:
            file_to_date[f.sources[0].file] = str(f.value)

    # Individual volume/folio facts (more precise than combined)
    vol_facts = _get_all_facts(store, "title.volume")
    folio_facts = _get_all_facts(store, "title.folio")
    file_to_vol: dict[str, str] = {
        f.sources[0].file: str(f.value) for f in vol_facts if f.sources
    }
    file_to_folio: dict[str, str] = {
        f.sources[0].file: str(f.value) for f in folio_facts if f.sources
    }

    lines: list[str] = []
    # seen key = normalised "vol_int/folio_int" — prevents zero-padding duplicates
    seen: set[str] = set()

    def _dedup_key(vol: str, folio: str) -> str:
        return f"{_norm_vol(vol)}/{_norm_vol(folio)}"

    # Prefer individual vol/folio facts (most precise)
    for src_file, vol in sorted(file_to_vol.items()):
        folio = file_to_folio.get(src_file, "")
        if not folio:
            continue
        key = _dedup_key(vol, folio)
        if key in seen:
            continue
        seen.add(key)
        date_str = _clean_date(file_to_date.get(src_file, _REVIEW_PLACEHOLDER))
        lines.append(f"- Register Search Statement Volume {vol} Folio {folio} dated {date_str}")

    # Fall back to volume_folio combined fact
    for f in vf_facts:
        vf = str(f.value)
        vol, folio = _parse_vol_folio(vf)
        if not folio:
            continue
        key = _dedup_key(vol, folio)
        if key in seen:
            continue
        seen.add(key)
        src_file = f.sources[0].file if f.sources else ""
        date_str = _clean_date(file_to_date.get(src_file, _REVIEW_PLACEHOLDER))
        lines.append(f"- Register Search Statement Volume {vol} Folio {folio} dated {date_str}")

    return lines


def _plan_entries(store) -> list[str]:
    """Build Plan of Subdivision and Plan of Consolidation lines.

    Title-derived entries (which carry the full LP/PS prefix) are processed
    first so that bare-number duplicates from DOCS_PLAN_OF_SUBDIVISION are
    suppressed via the normalized dedup key.
    """
    lines: list[str] = []
    # Keyed by _normalize_plan_num_key so 'LP009777' and '009777' collide.
    seen: set[str] = set()

    def _is_attachment_plan_source(fact: Fact) -> bool:
        src_file = _source_file(fact).lower()
        if not src_file:
            return True
        if "lease" in src_file or "notice" in src_file:
            return False
        return True

    # ── 1. Title-derived plan entries (processed FIRST so prefixed form wins) ──
    plan_type_facts = _get_all_facts(store, "title.plan_type")
    plan_num_facts = _get_all_facts(store, "title.plan_number")
    file_to_plan_type: dict[str, str] = {
        f.sources[0].file: str(f.value) for f in plan_type_facts if f.sources
    }
    file_to_plan_num: dict[str, str] = {
        f.sources[0].file: str(f.value) for f in plan_num_facts if f.sources
    }
    file_to_date = _title_dates_by_file(store)

    for src_file, plan_type in file_to_plan_type.items():
        plan_num = file_to_plan_num.get(src_file, "")
        if not plan_num:
            continue
        plan_type_u = plan_type.strip().upper()
        if "SUBDIVISION" in plan_type_u:
            plan_type_u = "PS"
        elif "CONSOLIDATION" in plan_type_u:
            plan_type_u = "PC"
        elif "LICENSED" in plan_type_u or plan_type_u == "LP":
            plan_type_u = "LP"
        elif "TITLE PLAN" in plan_type_u or plan_type_u == "TP":
            plan_type_u = "TP"

        if re.match(r"^(PS|PC|LP|TP|RP|SP|AL)\d", plan_num, re.IGNORECASE):
            full = plan_num.upper()
        elif plan_type_u in ("PS", "PC", "LP", "TP", "RP", "SP", "AL"):
            full = f"{plan_type_u}{plan_num}"
        else:
            continue

        dedup_key = _normalize_plan_num_key(full)
        date = _clean_date(file_to_date.get(src_file, _REVIEW_PLACEHOLDER))

        if plan_type_u == "PS" and dedup_key not in seen:
            seen.add(dedup_key)
            lines.append(f"- Plan of Subdivision {full} dated {date}")
        elif plan_type_u in ("LP", "TP") and dedup_key not in seen:
            seen.add(dedup_key)
            lines.append(f"- Plan of Subdivision {full} dated {date}")
        elif plan_type_u == "PC" and dedup_key not in seen:
            seen.add(dedup_key)
            lines.append(
                f"- Plan of Consolidation {_strip_plan_prefix(full, 'PC')} dated {date}"
            )

    # ── 2. DOCS_PLAN_OF_SUBDIVISION — skip if already seen via title facts ──
    ps_facts = _get_all_facts(store, P.DOCS_PLAN_OF_SUBDIVISION)
    for f in ps_facts:
        if not _is_attachment_plan_source(f):
            continue
        try:
            info = json.loads(str(f.value))
            num = info.get("number", _REVIEW_PLACEHOLDER)
            date = info.get("date", _REVIEW_PLACEHOLDER)
            dedup_key = _normalize_plan_num_key(str(num))
            if dedup_key not in seen:
                seen.add(dedup_key)
                n = str(num).strip().upper()
                _PLAN_PREFIXES = ("PS", "PC", "LP", "TP", "RP", "SP", "AL")
                full = n if any(n.startswith(p) for p in _PLAN_PREFIXES) else f"PS{n}"
                lines.append(f"- Plan of Subdivision {full} dated {date}")
        except (json.JSONDecodeError, TypeError):
            pass

    # ── 3. Plan of Consolidation ──
    pc_facts = _get_all_facts(store, P.DOCS_PLAN_OF_CONSOLIDATION)
    for f in pc_facts:
        if not _is_attachment_plan_source(f):
            continue
        try:
            info = json.loads(str(f.value))
            num = info.get("number", _REVIEW_PLACEHOLDER)
            date = info.get("date", _REVIEW_PLACEHOLDER)
            dedup_key = _normalize_plan_num_key(str(num))
            if dedup_key not in seen:
                seen.add(dedup_key)
                lines.append(
                    f"- Plan of Consolidation {_strip_plan_prefix(str(num), 'PC')} dated {date}"
                )
        except (json.JSONDecodeError, TypeError):
            pass

    return lines


def _mortgage_entries(store) -> list[str]:
    """Build mortgage lines from title encumbrances."""
    has_mortgage_fact, _ = store.get(P.TITLE_HAS_MORTGAGE)
    if not has_mortgage_fact or not has_mortgage_fact.value:
        return []

    count = _get_value(store, P.TITLE_ENCUMBRANCE_COUNT) or 0
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0

    lines: list[str] = []
    seen: set[str] = set()
    for idx in range(count):
        enc_type = _pick_fact_value(store, P.title_encumbrance(idx, "type"))
        if str(enc_type).upper() != "MORTGAGE":
            continue
        enc_number = _pick_fact_value(store, P.title_encumbrance(idx, "number")) or _REVIEW_PLACEHOLDER
        enc_date = _pick_fact_value(store, P.title_encumbrance(idx, "date")) or _REVIEW_PLACEHOLDER
        key = f"{enc_number}/{enc_date}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- Mortgage {enc_number} dated {_clean_date(str(enc_date))}")

    if not lines:
        lines.append(f"- Mortgage {_REVIEW_PLACEHOLDER} dated {_REVIEW_PLACEHOLDER}")
    return lines


def _covenant_entries(store) -> list[str]:
    """Build covenant lines, preferring covenant instrument docs when present."""
    has_covenant_fact, _ = store.get(P.TITLE_HAS_COVENANT)
    if not has_covenant_fact or not has_covenant_fact.value:
        return []

    lines: list[str] = []
    seen: set[str] = set()

    for fact in _get_all_facts(store, P.DOCS_COVENANT):
        try:
            info = json.loads(str(fact.value))
        except (json.JSONDecodeError, TypeError):
            continue
        number = str(info.get("number", _REVIEW_PLACEHOLDER))
        date = _clean_date(str(info.get("date", _REVIEW_PLACEHOLDER)))
        key = f"{number}/{date}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- Covenant {number} dated {date}")

    if lines:
        return lines

    count = _get_value(store, P.TITLE_ENCUMBRANCE_COUNT) or 0
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0

    for idx in range(count):
        enc_type = _pick_fact_value(store, P.title_encumbrance(idx, "type"))
        if str(enc_type).upper() != "COVENANT":
            continue
        enc_number = _pick_fact_value(store, P.title_encumbrance(idx, "number")) or _REVIEW_PLACEHOLDER
        enc_date = _pick_fact_value(store, P.title_encumbrance(idx, "date")) or _REVIEW_PLACEHOLDER
        key = f"{enc_number}/{enc_date}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- Covenant {enc_number} dated {_clean_date(str(enc_date))}")

    if not lines:
        lines.append(f"- Covenant {_REVIEW_PLACEHOLDER} dated {_REVIEW_PLACEHOLDER}")
    return lines


def _section_173_entries(store) -> list[str]:
    """Build Section 173 agreement lines from title encumbrances."""
    count = _get_value(store, P.TITLE_ENCUMBRANCE_COUNT) or 0
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0

    title_dates = _title_dates_by_file(store)
    lines: list[str] = []
    seen: set[str] = set()

    for idx in range(count):
        type_facts = _get_all_facts(store, P.title_encumbrance(idx, "type"))
        text_facts = _get_all_facts(store, P.title_encumbrance(idx, "text"))
        number = _pick_fact_value(store, P.title_encumbrance(idx, "number"))
        reg_date = _pick_fact_value(store, P.title_encumbrance(idx, "date"))

        enc_type = str(type_facts[0].value).upper() if type_facts else ""
        enc_text = str(text_facts[0].value).upper() if text_facts else ""
        if "AGREEMENT" not in enc_type and "SECTION 173" not in enc_text:
            continue
        if "SECTION 173" not in enc_text:
            continue
        if not number:
            continue

        src_file = ""
        for fact in type_facts + text_facts:
            src_file = _source_file(fact)
            if src_file:
                break

        search_date = title_dates.get(src_file, _clean_date(str(reg_date)))
        key = f"{number}/{search_date}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- Section 173 Agreement {number} dated {search_date}")

    return lines


def _building_permit_entries(store) -> list[str]:
    lines: list[str] = []
    for idx in range(12):
        kind = _get_value(store, P.building_permit(idx, "kind"))
        if kind is None:
            continue
        if not bool(_get_value(store, P.building_permit(idx, "within_last_7_years"))):
            continue
        issue_date = _clean_date(str(_get_value(store, P.building_permit(idx, "issue_date")) or _REVIEW_PLACEHOLDER))
        number = str(_get_value(store, P.building_permit(idx, "number")) or "").strip()
        label = "Occupancy Permit" if str(kind) == "occupancy_permit" else "Building Permit"
        if number:
            lines.append(f"- {label} No. {number} dated {issue_date}")
        else:
            lines.append(f"- {label} dated {issue_date}")
    return lines


def _water_entries(store) -> list[str]:
    """Build one water encumbrance certificate line per water document.

    Groups authority-name and date facts by source file so that multiple
    water documents (different authorities or different units) each get
    their own attachment line.
    """
    _PREFERRED_WATER_EXTRACTORS = (
        "rule:water_authority_certificate_v2",
        "rule:water_authority_certificate_v1",
        "ai:doc_extractor:water_authority_certificate_v1",
        "ai:doc_extractor:water_authority_certificate",
    )

    auth_facts = _get_all_facts(store, P.RATES_WATER_AUTHORITY)
    date_facts = _get_all_facts(store, P.DOCS_WATER_CERT_DATE)

    # Build file → best fact maps (prefer certificate extractors over vendor form)
    def _best_by_file(facts: list[Fact], preferred: tuple[str, ...]) -> dict[str, Fact]:
        result: dict[str, Fact] = {}
        for f in facts:
            file_name = _source_file(f) or "__no_file__"
            current = result.get(file_name)
            if current is None:
                result[file_name] = f
                continue
            # prefer certificate extractor over vendor_form
            current_rank = next((i for i, p in enumerate(preferred) if current.extractor == p), len(preferred))
            new_rank = next((i for i, p in enumerate(preferred) if f.extractor == p), len(preferred))
            if new_rank < current_rank or (new_rank == current_rank and f.confidence >= current.confidence):
                result[file_name] = f
        return result

    auth_by_file = _best_by_file(auth_facts, _PREFERRED_WATER_EXTRACTORS)
    date_by_file: dict[str, Fact] = {}
    for f in date_facts:
        file_name = _source_file(f) or "__no_file__"
        current = date_by_file.get(file_name)
        if current is None or f.confidence >= current.confidence:
            date_by_file[file_name] = f

    # Collect all file keys from both maps (a doc may have authority but no date or vice versa)
    all_files = sorted(set(auth_by_file) | set(date_by_file))

    if not all_files:
        # No water facts at all — output a single placeholder line
        return [f"- ## Water Encumbrance Certificate dated {_REVIEW_PLACEHOLDER}"]

    lines: list[str] = []
    seen_auth: set[str] = set()

    for file_name in all_files:
        auth_fact = auth_by_file.get(file_name)
        date_fact = date_by_file.get(file_name)

        raw_name = auth_fact.value if auth_fact else None
        water_name = _normalise_water_name(raw_name)

        date_str = _clean_date(str(date_fact.value)) if date_fact else _REVIEW_PLACEHOLDER

        # Deduplicate: same authority name across files (e.g. duplicate docs)
        dedup_key = (water_name or "").lower()
        if dedup_key and dedup_key in seen_auth:
            continue
        if dedup_key:
            seen_auth.add(dedup_key)

        if water_name:
            lines.append(f"- {water_name} Encumbrance Certificate dated {date_str}")
        else:
            lines.append(f"- ## Water Encumbrance Certificate dated {date_str}")

    return lines


def build_attachments_text(store) -> str:  # type: ignore[type-arg]
    """Build the complete formatted attachment list text for Tab 6 field 13.

    Returns a multi-line string starting with '- Due Diligence Checklist'.
    Items not present in the bundle use ## placeholders.
    """
    lines: list[str] = []

    # 1. Always first
    lines.append("- Due Diligence Checklist")

    # 2. Register Search Statements (one per title/folio)
    lines.extend(_title_entries(store))

    # 3. Plan of Subdivision + Plan of Consolidation
    lines.extend(_plan_entries(store))

    # 4. Mortgage (if applicable)
    lines.extend(_mortgage_entries(store))

    # 4b. Covenant (if applicable)
    lines.extend(_covenant_entries(store))

    # 4b. Section 173 agreements on title
    lines.extend(_section_173_entries(store))

    # 5. Owners Corporation documents
    title_has_oc_fact, _ = store.get(P.TITLE_HAS_OWNERS_CORPORATION)
    title_explicitly_no_oc = (
        title_has_oc_fact is not None and title_has_oc_fact.value is False
    )
    oc_exists_fact, _ = store.get(P.RATES_OWNERS_CORPORATION)
    oc_exists = bool(oc_exists_fact.value) if oc_exists_fact else False
    has_oc_basic = _has_fact(store, P.DOCS_OC_BASIC_REPORT_DATE)
    has_oc_cert = _has_fact(store, P.DOCS_OC_CERT_DATE)

    if not title_explicitly_no_oc and (oc_exists or has_oc_basic or has_oc_cert):
        oc_basic_date = _date_from_path(store, P.DOCS_OC_BASIC_REPORT_DATE)
        if has_oc_basic or oc_basic_date != _REVIEW_PLACEHOLDER:
            lines.append(_line_with_optional_date("Owners Corporation Basic Report", oc_basic_date))

        oc_cert_date = _date_from_path(store, P.DOCS_OC_CERT_DATE)
        if has_oc_cert or has_oc_basic or oc_exists:
            lines.append(_line_with_optional_date("Owners Corporation Certificate", oc_cert_date))

    # 6. Council certificates (named with council authority)
    council_name = _normalise_council_name(_get_value(store, P.RATES_COUNCIL_AUTHORITY))
    council_building_date = _date_from_path(store, P.DOCS_COUNCIL_BUILDING_APPROVAL_CERT_DATE)
    council_land_info_date = _date_from_path(store, P.DOCS_COUNCIL_LAND_INFO_CERT_DATE)
    if council_name:
        lines.append(
            f"- {council_name} Building Approval Certificate dated {council_building_date}"
        )
        lines.append(
            f"- {council_name} Land Information Certificate dated {council_land_info_date}"
        )
    else:
        lines.append(f"- ## Council Building Approval Certificate dated {council_building_date}")
        lines.append(f"- ## Council Land Information Certificate dated {council_land_info_date}")

    # 7. Water encumbrance certificate(s) — one line per water document/authority
    lines.extend(_water_entries(store))

    # 8. SRO Land Tax Certificate
    land_tax_date = _date_from_path(store, P.DOCS_LAND_TAX_CERT_DATE)
    lines.append(f"- State Revenue Office Land Tax Certificate dated {land_tax_date}")

    # 9. Detailed Property Report
    prop_report_date = _date_from_path(store, P.DOCS_PROPERTY_REPORT_DATE)
    lines.append(_line_with_optional_date("Detailed Property Report", prop_report_date))
    lines.extend(_building_permit_entries(store))

    # 10. Owner Builder Report — always mention it; add the extracted date when present.
    owner_builder_date = _date_from_path(store, P.DOCS_OWNER_BUILDER_REPORT_DATE)
    if owner_builder_date == _REVIEW_PLACEHOLDER:
        lines.append(f"- Owner Builder Report {_OWNER_BUILDER_PLACEHOLDER}")
    else:
        lines.append(f"- Owner Builder Report dated {owner_builder_date}")

    # 11. Domestic Building Insurance (if building warranty doc present)
    bldg_warranty_date = _date_from_path(store, P.DOCS_BLDG_WARRANTY_DATE)
    if bldg_warranty_date != _REVIEW_PLACEHOLDER:
        lines.append(f"- Domestic Building Insurance dated {bldg_warranty_date}")

    # 12. VicRoads Certificate
    vicroads_date = _date_from_path(store, P.DOCS_VICROADS_CERT_DATE)
    lines.append(f"- Vic Roads Certificate dated {vicroads_date}")

    # 13. EPA Certificate
    epa_date = _date_from_path(store, P.DOCS_EPA_CERT_DATE)
    lines.append(f"- EPA Certificate dated {epa_date}")

    # 13b. Residential Tenancy Agreement
    tenancy_date = _date_from_path(store, P.DOCS_RESIDENTIAL_TENANCY_AGREEMENT_DATE)
    if tenancy_date != _REVIEW_PLACEHOLDER:
        lines.append(f"- Residential Tenancy Agreement dated {tenancy_date}")

    # 14. s27 Notice of Deposit Release — only if vendor said YES on vendor form.
    s27_fact, _ = store.get("sale.s27_early_release")
    if s27_fact is not None and s27_fact.value is True:
        lines.append("- s27 Notice of Deposit Release (by way of service)")

    return "\n".join(lines)
