"""Scope decision: which Sec 32 questions to attempt filling for a given matter.

Scope is derived from the final_output.  If the vendor said No to building
works, the building permit questions are out of scope.  If the property has no
owners corporation, those questions are skipped.

Questions outside scope are left null in the answered template and flagged as
"out_of_scope" in the mapping report — they are not forwarded to the AI either.
"""
from __future__ import annotations

from typing import Any


def _get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


# ── Always in scope ──────────────────────────────────────────────────────────
# These questions are filled for every matter regardless of circumstances.

ALWAYS_IN_SCOPE: frozenset[str] = frozenset({
    # Tab 1 — rates, outgoings, sale details
    "s32_1.rates_in_attached_certificates",
    "s32_1.rates_their_amounts_are",
    "s32_1.rates_total_does_not_exceed_check",
    "s32_1.rates_total_does_not_exceed_amount",
    "s32_1.outgoing_1_authority",
    "s32_1.outgoing_1_amount",
    "s32_1.outgoing_1_interest",
    "s32_1.outgoing_2_authority",
    "s32_1.outgoing_2_amount",
    "s32_1.no_amounts_beyond_listed",
    "s32_1.sale_subject_to_mortgage",
    "s32_1.land_tax_reform_scheme_land",
    # Tab 2 — easements, planning (mandatory Sec 32 disclosures)
    "s32_2.easements_in_title_documents",
    "s32_2.easement_failure_to_comply_check",
    "s32_2.easement_failure_to_comply_text",
    "s32_2.road_access_none",
    "s32_2.bushfire_prone_area",
    "s32_2.planning_certificate_attached",
    "s32_2.planning_scheme_name",
    "s32_2.planning_responsible_authority",
    "s32_2.planning_zone",
    "s32_2.planning_overlay_name",
    # Tab 3 — notices (always checked; we don't have a reliable skip signal)
    "s32_3.notices_in_attached_certificates",
    "s32_3.notices_as_follows_check",
    "s32_3.notices_as_follows_text",
    # Tab 4 — services (always disclosed)
    "s32_4.service_electricity_not_connected",
    "s32_4.service_gas_not_connected",
    "s32_4.service_water_not_connected",
    "s32_4.service_sewerage_not_connected",
    "s32_4.service_telephone_not_connected",
    # Tab 4 — GAIC trigger (always check whether it applies)
    "s32_4.gaic_applies",
    # Tab 6 — attachments (always)
    "s32_6.due_diligence_checklist",
    "s32_6.attachments_list",
})

# ── Conditional scopes ────────────────────────────────────────────────────────

# Damage/destruction insurance — only if property is insured
INSURANCE_SCOPE: frozenset[str] = frozenset({
    "s32_1.damage_destruction_policy_attached",
    "s32_1.damage_destruction_particulars",
    "s32_1.insurance_company_name",
    "s32_1.insurance_policy_type",
    "s32_1.insurance_policy_no",
    "s32_1.insurance_amount_insured",
})

# Owner builder insurance — only if improvements made in last 6.5 years
OWNER_BUILDER_SCOPE: frozenset[str] = frozenset({
    "s32_1.owner_builder_policy_attached",
    "s32_1.owner_builder_required_insurance",
})

# Building permits — only if vendor indicated permits were issued
BUILDING_PERMIT_SCOPE: frozenset[str] = frozenset({
    "s32_3.building_permits_in_attached_cert",
    "s32_3.building_permits_as_follows_check",
    "s32_3.building_permits_as_follows_text",
})

# Owners corporation — only if OC exists for this property
OWNERS_CORP_SCOPE: frozenset[str] = frozenset({
    "s32_4.oc_certificate_attached",
    "s32_4.oc_information_prescribed_attached",
    "s32_4.oc_inactive",
})

# GAIC detail — only if GAIC trigger is true
GAIC_DETAIL_SCOPE: frozenset[str] = frozenset({
    "s32_4.gaic_work_in_kind_land_transferred",
    "s32_4.gaic_work_in_kind_works_carried_out",
    "s32_4.gaic_land_imposed",
    "s32_4.gaic_cert_release_from_liability",
    "s32_4.gaic_cert_deferral",
    "s32_4.gaic_cert_exemption",
    "s32_4.gaic_cert_staged_payment_approval",
    "s32_4.gaic_cert_no_liability",
    "s32_4.gaic_notice_reduction_or_exemption",
    "s32_4.gaic_cert_part_9b",
})


# ── Public API ────────────────────────────────────────────────────────────────

def build_scope(final_output: dict[str, Any]) -> frozenset[str]:
    """Return the set of question_ids that should be filled for this matter.

    Start with ALWAYS_IN_SCOPE and expand based on what the vendor form says.
    Any question_id not in the returned set will be left null and reported as
    out_of_scope — not forwarded to the AI confirmation pass.
    """
    scope: set[str] = set(ALWAYS_IN_SCOPE)

    # Insurance fields: include if property is insured
    if _get(final_output, "property_details.insured.value") is True:
        scope |= INSURANCE_SCOPE

    # Owner builder insurance: include if improvements made in last 6.5 years
    if _get(final_output, "planning_building_permits.improved_past_6_5_years.value") is True:
        scope |= OWNER_BUILDER_SCOPE

    # Building permits: include if vendor said permits were issued
    if _get(final_output, "planning_building_permits.building_permits_last_7_years.value") is True:
        scope |= BUILDING_PERMIT_SCOPE

    # Owners corporation: include if OC applies to this property
    if _get(final_output, "rates_taxes_charges.owners_corporation.value") is True:
        scope |= OWNERS_CORP_SCOPE

    # GAIC detail: include only if GAIC trigger is confirmed
    if _get(final_output, "planning_building_permits.gaic_trigger.value") is True:
        scope |= GAIC_DETAIL_SCOPE

    return frozenset(scope)
