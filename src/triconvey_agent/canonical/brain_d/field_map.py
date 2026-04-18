"""Static mapping: question_id → TriConvey field descriptor(s).

Each entry in FIELD_MAP tells Brain D how to find the right field in the
YAML tab files and what action to produce.

FieldBinding fields
-------------------
tab_name        : matches tab_name in the YAML file
control_type    : "CheckBox" | "Edit" | "ComboBox"
match_name      : exact match on field.name (most reliable)
match_label     : exact match on field.nearby_label
match_top       : position.top within ±3 px (for outgoing rows)
match_left_min  : position.left >= this (to pick the right column)
match_left_max  : position.left <= this
action          : "set_checkbox" | "set_text" | "select_dropdown"
value_adapter   : how to convert AnswerObject.value → payload
    "direct"     : use value as-is
    "bool_tick"  : True → tick on, False → tick off
    "str"        : str(value)
    "dollar_strip": strip leading '$', keep the rest
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldBinding:
    tab_name: str
    control_type: str
    action: str
    value_adapter: str = "direct"
    match_name: str | None = None
    match_label: str | None = None
    match_top: int | None = None
    match_left_min: int | None = None
    match_left_max: int | None = None


# Positional reference for Sec. 32 (1) outgoing rows (from YAML):
#   Row 1: top=282  — council
#   Row 2: top=308  — water
#   Row 3: top=333  — land tax
#   Row 4: top=360  — other
#
# Within each row, left ranges identify the column:
#   Authority column:  left ~ -1871  (width ~407)
#   Amount column:     left ~ -1449  (width ~134)
#   Outstanding/Int:   left ~ -1300  (width ~129)

FIELD_MAP: dict[str, list[FieldBinding]] = {
    # -----------------------------------------------------------------------
    # Sec. 32 (1) — Outgoing rows
    # -----------------------------------------------------------------------
    "sec32_1.1_outgoing_1_authority": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=282, match_left_min=-1875, match_left_max=-1460,
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_1.1_outgoing_1_amount": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=282, match_left_min=-1455, match_left_max=-1310,
            action="set_text", value_adapter="dollar_strip",
        )
    ],
    "sec32_1.1_outgoing_2_authority": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=308, match_left_min=-1875, match_left_max=-1460,
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_1.1_outgoing_2_amount": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=308, match_left_min=-1455, match_left_max=-1310,
            action="set_text", value_adapter="dollar_strip",
        )
    ],
    "sec32_1.1_outgoing_3_authority": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=333, match_left_min=-1875, match_left_max=-1460,
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_1.1_outgoing_3_amount": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=333, match_left_min=-1455, match_left_max=-1310,
            action="set_text", value_adapter="dollar_strip",
        )
    ],

    # ---- 1.3 / 1.4 Contract type ----------------------------------------
    "sec32_1.3_terms_contract": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="CheckBox",
            match_name="Terms Contract",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_1.4_sale_subject_to_mortgage": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="CheckBox",
            match_name="Sale Subject to Mortgage",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],

    # ---- 2.1 Insurance ---------------------------------------------------
    "sec32_2.1_insurance_company": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_label="Company Name",
            match_top=675,
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_2.1_insurance_policy_no": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_label="Policy No.",
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_2.1_insurance_type": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_label="Type of Policy",
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_2.1_insurance_amount_insured": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_label="Amount Insured",
            action="set_text", value_adapter="dollar_strip",
        )
    ],

    # -----------------------------------------------------------------------
    # Sec. 32 (2) — Title, Road, Planning
    # -----------------------------------------------------------------------
    "sec32_3.2_no_road_access": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="CheckBox",
            match_name="No road access",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_3.3_bushfire_prone": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="CheckBox",
            match_name="Land is in Designated Bushfire Prone Area",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_3.4_planning_scheme": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="Edit",
            match_label="Name of planning scheme",
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_3.4_responsible_authority": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="Edit",
            match_label="Name of responsible authority",
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_3.4_planning_zone": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="ComboBox",
            match_label="Name of responsible authority",
            action="select_dropdown", value_adapter="str",
        )
    ],
    "sec32_3.4_planning_overlay_name": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="Edit",
            match_label="Name of planning overlay",
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_3.1_easement_failure_text": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="Edit",
            match_label="Particulars of any existing failure to comply with easement, "
                        "covenant or other similar restriction are:",
            action="set_text", value_adapter="str",
        )
    ],

    # -----------------------------------------------------------------------
    # Sec. 32 (3) — Notices
    # -----------------------------------------------------------------------
    "sec32_4.1_notices_text": [
        FieldBinding(
            tab_name="Sec. 32 (3)", control_type="Edit",
            match_label="Are as follows:",
            match_top=281,
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_5.1_building_permits_text": [
        FieldBinding(
            tab_name="Sec. 32 (3)", control_type="CheckBox",
            match_name="Are as follows:",
            match_top=647,
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_5.1_building_permits_details": [
        FieldBinding(
            tab_name="Sec. 32 (3)", control_type="Edit",
            match_label="Are as follows:",
            match_top=671,
            action="set_text", value_adapter="str",
        )
    ],

    # -----------------------------------------------------------------------
    # Sec. 32 (4) — Services
    # -----------------------------------------------------------------------
    "sec32_8_electricity_not_connected": [
        FieldBinding(
            tab_name="Sec. 32 (4)", control_type="CheckBox",
            match_name="Electricity supply",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_8_gas_not_connected": [
        FieldBinding(
            tab_name="Sec. 32 (4)", control_type="CheckBox",
            match_name="Gas supply",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_8_water_not_connected": [
        FieldBinding(
            tab_name="Sec. 32 (4)", control_type="CheckBox",
            match_name="Water supply",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_8_sewerage_not_connected": [
        FieldBinding(
            tab_name="Sec. 32 (4)", control_type="CheckBox",
            match_name="Sewerage",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_8_telephone_not_connected": [
        FieldBinding(
            tab_name="Sec. 32 (4)", control_type="CheckBox",
            match_name="Telephone services",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],

    # ---- GAIC / Owners Corp ---------------------------------------------
    "sec32_7_gaic_applies": [
        FieldBinding(
            tab_name="Sec. 32 (4)", control_type="CheckBox",
            match_name="GAIC applies",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "sec32_oc_inactive": [
        FieldBinding(
            tab_name="Sec. 32 (4)", control_type="CheckBox",
            match_name="Owners Corporation is inactive",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "policy_4_oc_cert_attached": [
        FieldBinding(
            tab_name="Sec. 32 (4)", control_type="CheckBox",
            match_name="Attached is a current owners corporation certificate "
                        "issued according to s151 of the Owners Corporations Act",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],

    # -----------------------------------------------------------------------
    # Sec. 32 (1) — Policy checkboxes + OC row 4
    # -----------------------------------------------------------------------
    "policy_1_amounts_are_checked": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="CheckBox",
            match_name="Their amounts are:",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "policy_1_certs_attached": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="CheckBox",
            match_name="Are contained in attached certificate(s)",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "policy_1_total_does_not_exceed": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="CheckBox",
            match_name="Their total does not exceed",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "policy_1_total_does_not_exceed_amount": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=226, match_left_min=-1305, match_left_max=-1165,
            action="set_text", value_adapter="dollar_strip",
        )
    ],

    # Outgoing row 4: Owners Corporation fees (top=360)
    "sec32_1.1_outgoing_4_authority": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=360, match_left_min=-1875, match_left_max=-1460,
            action="set_text", value_adapter="str",
        )
    ],
    "sec32_1.1_outgoing_4_amount": [
        FieldBinding(
            tab_name="Sec. 32 (1)", control_type="Edit",
            match_top=360, match_left_min=-1455, match_left_max=-1310,
            action="set_text", value_adapter="dollar_strip",
        )
    ],

    # -----------------------------------------------------------------------
    # Sec. 32 (2) — Policy checkboxes + failure text
    # -----------------------------------------------------------------------
    "policy_2_title_in_attached": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="CheckBox",
            match_name="Is in the attached copies of title documents",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "policy_2_failure_checked": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="CheckBox",
            match_name="Particulars of any existing failure to comply with easement, "
                        "covenant or other similar restriction are:",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],
    "policy_2_failure_text": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="Edit",
            match_label="Particulars of any existing failure to comply with "
                        "easement, covenant or other similar restriction are:",
            action="set_text", value_adapter="str",
        )
    ],
    "policy_2_planning_cert_attached": [
        FieldBinding(
            tab_name="Sec. 32 (2)", control_type="CheckBox",
            match_name="Certificate with required information attached",
            action="set_checkbox", value_adapter="bool_tick",
        )
    ],

    # -----------------------------------------------------------------------
    # Sec. 32 (6) — Due Diligence + Attachments
    # -----------------------------------------------------------------------
    "policy_6_due_diligence": [
        FieldBinding(
            tab_name="Sec. 32 (6)", control_type="Edit",
            match_top=256,
            action="set_text", value_adapter="str",
        )
    ],
    "policy_6_attachments": [
        FieldBinding(
            tab_name="Sec. 32 (6)", control_type="Edit",
            match_top=424,
            action="set_text", value_adapter="str",
        )
    ],
}
