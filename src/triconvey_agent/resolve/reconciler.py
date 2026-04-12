"""Field-by-field reconciliation of rules-based and AI-based extractions.

Decision logic per field
------------------------

Rules confidence threshold constants:
  RULES_DOMINANT  = 0.90  rules very confident → always keep rules
  AI_MIN          = 0.60  AI effective_confidence below this → ignore AI
  AGREE_BOOST     = 0.04  added to rules confidence when both agree

For each field:

1. No AI result (AI skipped, error, or doc type unsupported)
   → keep rules unchanged

2. AI quote not verified or AI effective_confidence < AI_MIN
   → likely hallucination → keep rules, don't even surface a conflict

3. Rules field is null (rules missed it) AND AI is trustworthy
   → accept AI → set field from AI, tag extractor "ai_grounded_fill"

4. Rules confidence >= RULES_DOMINANT
   a. Both agree  → boost rules confidence slightly, keep rules
   b. Disagree    → keep rules, flag conflict for human review

5. Rules confidence < RULES_DOMINANT (rules uncertain)
   a. Both agree  → boost rules confidence slightly, keep rules
   b. AI significantly more confident (delta > 0.10)
      → accept AI, tag extractor "ai_grounded_correction"
   c. Otherwise   → keep rules, flag conflict for human review

Self-clarification
------------------
When conflicts exist the reconciler writes them to
`final_extraction.review_summary["ai_conflicts"]` so the mapping step and
human review can both surface them.  A future step can batch all conflicts
into a single "arbitration" AI call for fully automated resolution.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from triconvey_agent.schemas.ai_extract import AIDocumentExtract, GroundedValue
from triconvey_agent.schemas.documents import DocumentType, SourceSpan
from triconvey_agent.schemas.extracted import ConflictItem, Evidence, FieldValue, FinalExtraction

# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------

RULES_DOMINANT  = 0.90   # rules confidence above which rules always win
AI_MIN          = 0.60   # AI effective_confidence below which AI is ignored
AGREE_BOOST     = 0.04   # confidence added when both agree


# ---------------------------------------------------------------------------
# Mapping: AI field → (FinalExtraction section attr, field key)
# ---------------------------------------------------------------------------

# Vendor form services: (services sub-field name, section, key)
_SERVICE_MAP: list[tuple[str, str]] = [
    ("mains_electricity", "mains_electricity"),
    ("mains_gas",         "mains_gas"),
    ("mains_water",       "mains_water"),
    ("mains_sewerage",    "mains_sewerage"),
    ("telephone_or_nbn",  "nbn"),          # AI unifies telephone+NBN → maps to nbn key
    ("bottled_gas",       "bottled_gas"),
    ("tank_water",        "tank_water"),
    ("septic_tank",       "septic_tank"),
]

# Vendor form scalar fields: (AI attr name, section attr, key)
_VENDOR_SCALAR_MAP: list[tuple[str, str, str]] = [
    ("council",                       "rates_taxes_charges",       "council"),
    ("water_authority",               "rates_taxes_charges",       "water_authority"),
    ("council_rates",                 "rates_taxes_charges",       "council_rates"),
    ("water_rates",                   "rates_taxes_charges",       "water_rates"),
    ("owners_corporation",            "rates_taxes_charges",       "owners_corporation"),
    ("insured",                       "property_details",          "insured"),
    ("bushfire_prone_area",           "planning_building_permits", "bushfire_prone_area"),
    ("gaic_trigger",                  "planning_building_permits", "gaic_trigger"),
    ("building_permits_last_7_years", "planning_building_permits", "building_permits_last_7_years"),
    ("improved_past_6_5_years",       "planning_building_permits", "improved_past_6_5_years"),
]

# VIC title fields: (AI attr name, key in vic_title_extract)
_TITLE_MAP: list[tuple[str, str]] = [
    ("volume_folio",          "primary_volume_folio"),
    ("plan_number",           "plan_number"),
    ("plan_type",             "plan_type"),
    ("registered_proprietor", "registered_proprietor"),
    ("lot_description",       "land_description"),
]


# ---------------------------------------------------------------------------
# Core reconcile-one-field logic
# ---------------------------------------------------------------------------

def _make_ai_field(gv: GroundedValue, source_file: str, doc_type: str, key: str, extractor_tag: str) -> FieldValue:
    """Build a FieldValue from a verified GroundedValue."""
    evidence = Evidence(
        source_file=source_file,
        source_type=DocumentType(doc_type) if doc_type in DocumentType._value2member_map_ else None,
        span=SourceSpan(label=key),
        snippet=gv.quote,
        label=key,
    )
    return FieldValue(
        value=gv.value,
        confidence=gv.effective_confidence(),
        extractor=extractor_tag,
        normalized_key=key,
        evidence=[evidence],
    )


def _values_agree(rules_val: Any, ai_val: Any) -> bool:
    """Check whether two extracted values are semantically equivalent."""
    if rules_val is None and ai_val is None:
        return True
    if rules_val is None or ai_val is None:
        return False
    if isinstance(rules_val, bool) and isinstance(ai_val, bool):
        return rules_val == ai_val
    # String comparison: normalise whitespace and case
    rv = str(rules_val).strip().lower()
    av = str(ai_val).strip().lower()
    return rv == av or rv in av or av in rv


def reconcile_field(
    section_dict: dict[str, FieldValue],
    key: str,
    gv: GroundedValue,
    source_file: str,
    doc_type: str,
    conflicts: list[dict[str, Any]],
) -> None:
    """Reconcile one field in-place, appending to conflicts list if needed."""
    ai_eff = gv.effective_confidence()

    # Rule 2: ignore low-quality AI results
    if ai_eff < AI_MIN:
        return

    rules_field: FieldValue | None = section_dict.get(key)
    rules_conf  = rules_field.confidence if rules_field else 0.0
    rules_val   = rules_field.value if rules_field else None

    # Rule 3: rules missed it — accept AI fill
    if rules_field is None or rules_val is None:
        section_dict[key] = _make_ai_field(gv, source_file, doc_type, key, "ai_grounded_fill")
        return

    agree = _values_agree(rules_val, gv.value)

    if rules_conf >= RULES_DOMINANT:
        # Rule 4a: both agree, rules dominant
        if agree:
            section_dict[key] = rules_field.model_copy(
                update={"confidence": min(0.99, rules_conf + AGREE_BOOST)}
            )
        else:
            # Rule 4b: disagree — keep rules, flag conflict
            conflicts.append({
                "key": key,
                "rules_value": rules_val,
                "rules_confidence": rules_conf,
                "ai_value": gv.value,
                "ai_confidence": ai_eff,
                "ai_quote": gv.quote,
                "source_file": source_file,
                "resolution": "rules_kept_dominant",
            })
        return

    # Rule 5: rules uncertain
    if agree:
        # 5a: boost
        section_dict[key] = rules_field.model_copy(
            update={"confidence": min(0.99, rules_conf + AGREE_BOOST)}
        )
    elif ai_eff > rules_conf + 0.10:
        # 5b: AI significantly more confident — accept AI correction
        old_field = deepcopy(rules_field)
        new_field = _make_ai_field(gv, source_file, doc_type, key, "ai_grounded_correction")
        # Keep rules evidence as a conflict record on the new field
        new_field.conflicts.append(
            ConflictItem(
                source_file=old_field.evidence[0].source_file if old_field.evidence else source_file,
                source_type=old_field.evidence[0].source_type if old_field.evidence else None,
                value=rules_val,
                reason="overridden by ai_grounded_correction",
                evidence=old_field.evidence,
            )
        )
        section_dict[key] = new_field
        conflicts.append({
            "key": key,
            "rules_value": rules_val,
            "rules_confidence": rules_conf,
            "ai_value": gv.value,
            "ai_confidence": ai_eff,
            "ai_quote": gv.quote,
            "source_file": source_file,
            "resolution": "ai_correction_applied",
        })
    else:
        # 5c: keep rules, flag conflict
        conflicts.append({
            "key": key,
            "rules_value": rules_val,
            "rules_confidence": rules_conf,
            "ai_value": gv.value,
            "ai_confidence": ai_eff,
            "ai_quote": gv.quote,
            "source_file": source_file,
            "resolution": "rules_kept_uncertain",
        })


# ---------------------------------------------------------------------------
# Per-document-type reconciliation
# ---------------------------------------------------------------------------

def _reconcile_vendor_form(
    extraction: FinalExtraction,
    ai_doc: AIDocumentExtract,
    conflicts: list[dict[str, Any]],
) -> None:
    vf = ai_doc.vendor_form
    if vf is None:
        return

    # Services
    for ai_attr, section_key in _SERVICE_MAP:
        gv: GroundedValue = getattr(vf.services, ai_attr, GroundedValue())
        reconcile_field(
            extraction.services_connected, section_key,
            gv, ai_doc.source_file, ai_doc.document_type, conflicts,
        )

    # Scalar vendor form fields
    for ai_attr, section_name, section_key in _VENDOR_SCALAR_MAP:
        gv = getattr(vf, ai_attr, GroundedValue())
        section: dict[str, FieldValue] = getattr(extraction, section_name)
        reconcile_field(
            section, section_key,
            gv, ai_doc.source_file, ai_doc.document_type, conflicts,
        )


def _reconcile_vic_title(
    extraction: FinalExtraction,
    ai_doc: AIDocumentExtract,
    conflicts: list[dict[str, Any]],
) -> None:
    vt = ai_doc.vic_title
    if vt is None:
        return

    for ai_attr, section_key in _TITLE_MAP:
        gv: GroundedValue = getattr(vt, ai_attr, GroundedValue())
        reconcile_field(
            extraction.vic_title_extract, section_key,
            gv, ai_doc.source_file, ai_doc.document_type, conflicts,
        )


def _reconcile_rates_cert(
    extraction: FinalExtraction,
    ai_doc: AIDocumentExtract,
    conflicts: list[dict[str, Any]],
) -> None:
    rc = ai_doc.rates_cert
    if rc is None:
        return

    # Authority name goes into rates section
    reconcile_field(
        extraction.rates_taxes_charges, "water_authority",
        rc.authority_name, ai_doc.source_file, ai_doc.document_type, conflicts,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def reconcile_extractions(
    extraction: FinalExtraction,
    ai_results: list[AIDocumentExtract],
) -> FinalExtraction:
    """Merge AI extraction results into the rules-based FinalExtraction.

    Modifies extraction in place and writes conflicts + a summary to
    extraction.review_summary["ai_reconciliation"].

    Returns the (mutated) extraction.
    """
    if not ai_results:
        return extraction

    conflicts: list[dict[str, Any]] = []
    fills = corrections = skipped_errors = 0

    for ai_doc in ai_results:
        if ai_doc.extraction_error:
            skipped_errors += 1
            continue

        if ai_doc.vendor_form is not None:
            before_counts = _count_non_null(extraction)
            _reconcile_vendor_form(extraction, ai_doc, conflicts)
            after_counts  = _count_non_null(extraction)
            fills += max(0, after_counts - before_counts)

        if ai_doc.vic_title is not None:
            _reconcile_vic_title(extraction, ai_doc, conflicts)

        if ai_doc.rates_cert is not None:
            _reconcile_rates_cert(extraction, ai_doc, conflicts)

    for c in conflicts:
        if c.get("resolution") == "ai_correction_applied":
            corrections += 1

    # Attach a reconciliation report to review_summary
    existing = extraction.review_summary or {}
    existing["ai_reconciliation"] = {
        "ai_docs_processed": len(ai_results),
        "ai_docs_errored": skipped_errors,
        "fields_filled_by_ai": fills,
        "fields_corrected_by_ai": corrections,
        "conflicts_found": len(conflicts),
        "conflicts": conflicts,
    }
    extraction.review_summary = existing
    return extraction


# ---------------------------------------------------------------------------
# Agent-mode reconciliation (PropertyKnowledgeBase → FinalExtraction)
# ---------------------------------------------------------------------------

# Maps (kb_section, kb_key) → (extraction_section_attr, extraction_key)
_AGENT_FIELD_MAP: list[tuple[str, str, str, str]] = [
    # services
    ("services", "mains_electricity", "services_connected", "mains_electricity"),
    ("services", "mains_gas",         "services_connected", "mains_gas"),
    ("services", "mains_water",       "services_connected", "mains_water"),
    ("services", "mains_sewerage",    "services_connected", "mains_sewerage"),
    ("services", "telephone_or_nbn",  "services_connected", "nbn"),
    ("services", "bottled_gas",       "services_connected", "bottled_gas"),
    ("services", "tank_water",        "services_connected", "tank_water"),
    ("services", "septic_tank",       "services_connected", "septic_tank"),
    # rates
    ("rates", "council_annual",     "rates_taxes_charges", "council_rates"),
    ("rates", "water_annual",       "rates_taxes_charges", "water_rates"),
    ("rates", "land_tax_annual",    "rates_taxes_charges", "land_tax"),
    ("rates", "council_authority",  "rates_taxes_charges", "council"),
    ("rates", "water_authority",    "rates_taxes_charges", "water_authority"),
    # planning
    ("planning", "zone",                       "planning_building_permits", "planning_zones"),
    ("planning", "bushfire_prone",             "planning_building_permits", "bushfire_prone_area"),
    ("planning", "gaic_applies",               "planning_building_permits", "gaic_trigger"),
    ("planning", "building_permits_last_7yrs", "planning_building_permits", "building_permits_last_7_years"),
    ("planning", "owners_corporation_exists",  "rates_taxes_charges",       "owners_corporation"),
    # property
    ("property", "council",  "rates_taxes_charges", "council"),
    ("property", "address",  "property_details",    "street_address"),
    # vendor
    ("vendor", "full_name", "vendor_core", "vendor_full_name"),
    ("vendor", "address",   "vendor_core", "vendor_address"),
    ("vendor", "phone",     "vendor_core", "vendor_phone"),
    ("vendor", "email",     "vendor_core", "vendor_email"),
    ("vendor", "is_trustee","vendor_core", "is_trustee"),
    ("vendor", "trust_name","trustee",     "trust_name"),
]


def _kb_field_to_grounded_value(field: dict[str, Any] | None) -> GroundedValue:
    """Convert a kb field dict to a GroundedValue for reconciliation."""
    if not field or field.get("value") is None:
        return GroundedValue()
    return GroundedValue(
        value=field["value"],
        quote=field.get("quote"),
        confidence=float(field.get("confidence", 0.0)),
        quote_verified=bool(field.get("quote_verified", False)),
    )


def reconcile_from_agent(
    extraction: FinalExtraction,
    kb_raw: dict[str, Any],
) -> FinalExtraction:
    """Merge a PropertyKnowledgeBase into rules-based FinalExtraction.

    Uses the same reconcile_field() logic as reconcile_extractions().
    Writes summary to review_summary["agent_reconciliation"].
    """
    conflicts: list[dict[str, Any]] = []
    fills = corrections = 0

    for kb_section, kb_key, ext_section, ext_key in _AGENT_FIELD_MAP:
        field_dict = kb_raw.get(kb_section, {}).get(kb_key)
        gv = _kb_field_to_grounded_value(field_dict)
        if gv.value is None:
            continue

        source_file = (field_dict or {}).get("source_file", "property_agent")
        before_section: dict[str, FieldValue] = getattr(extraction, ext_section, {})
        had_value = before_section.get(ext_key) is not None and before_section.get(ext_key, FieldValue()).value is not None  # type: ignore[union-attr]

        reconcile_field(
            before_section, ext_key,
            gv, source_file, "property_agent", conflicts,
        )

        now_section: dict[str, FieldValue] = getattr(extraction, ext_section, {})
        now_fv = now_section.get(ext_key)
        if now_fv and now_fv.extractor == "ai_grounded_fill" and not had_value:
            fills += 1

    for c in conflicts:
        if c.get("resolution") == "ai_correction_applied":
            corrections += 1

    # Record agent conflicts and missing-info in review_summary
    existing = extraction.review_summary or {}
    existing["agent_reconciliation"] = {
        "fields_filled_by_agent": fills,
        "fields_corrected_by_agent": corrections,
        "conflicts_detected": len(conflicts),
        "conflicts": conflicts,
        "missing_information": kb_raw.get("missing_information", []),
        "agent_conflicts_cross_doc": kb_raw.get("conflicts", []),
        "audit_summary": kb_raw.get("audit_summary", {}),
    }
    extraction.review_summary = existing
    return extraction


def _count_non_null(extraction: FinalExtraction) -> int:
    """Count non-null FieldValue entries across the key sections."""
    total = 0
    for section_name in (
        "services_connected", "rates_taxes_charges",
        "planning_building_permits", "property_details", "vic_title_extract",
    ):
        section: dict[str, FieldValue] = getattr(extraction, section_name, {})
        for fv in section.values():
            if isinstance(fv, FieldValue) and fv.value is not None:
                total += 1
    return total
