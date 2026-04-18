"""Canonical fact path namespace.

Centralised so every extractor uses the same dot paths and questions in
the registry can reference them without typos.

Conventions
-----------
- Lowercase, dot-separated, snake_case segments
- Plural for collections (vendors, overlays), singular for objects
- Multi-instance entities use indexed paths: vendor.0.first_name, vendor.1.first_name
- Booleans are at a leaf, e.g. services.electricity.connected (true/false/null)
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Vendor identity (multi-instance — indexed)
# ---------------------------------------------------------------------------

def vendor(index: int, field: str) -> str:
    """Return e.g. 'vendor.0.first_name'."""
    return f"vendor.{index}.{field}"


VENDOR_FIELDS = (
    "title",
    "first_name",
    "middle_name",
    "last_name",
    "preferred_name",
    "occupation",
    "residential_address",
    "phone",
    "email",
    "country_of_citizenship",
    "nationality",
)

VENDOR_COUNT = "vendor.count"

# ---------------------------------------------------------------------------
# Trust (single object)
# ---------------------------------------------------------------------------

VENDOR_IS_TRUST = "vendor.is_trust"
TRUST_NAME = "vendor.trust.name"
TRUST_TYPE = "vendor.trust.type"
TRUST_BENEFICIARIES = "vendor.trust.beneficiaries"
TRUST_ABN = "vendor.trust.abn"

# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------

PROPERTY_ADDRESS = "property.address"
PROPERTY_NATURE = "property.nature"
PROPERTY_DWELLING_AGE = "property.dwelling_age"
PROPERTY_VOLUME_OR_BOOK = "property.volume_or_book"
PROPERTY_FOLIO_OR_NUMBER = "property.folio_or_number"
PROPERTY_LOT_NUMBER = "property.lot_number"
PROPERTY_PLAN_NUMBER = "property.plan_number"
PROPERTY_OWNER_OCCUPIED = "property.owner_occupied"
PROPERTY_LEASED = "property.leased"
PROPERTY_INSURED = "property.insured"

# ---------------------------------------------------------------------------
# Services connected to the land
# ---------------------------------------------------------------------------

def service_connected(name: str) -> str:
    """Return e.g. 'services.electricity.connected'."""
    return f"services.{name}.connected"


SERVICE_NAMES = (
    "electricity",
    "gas",
    "water",
    "sewerage",
    "telephone",
    "nbn",
    "bottled_gas",
    "tank_water",
    "septic_tank",
)

# ---------------------------------------------------------------------------
# Rates / outgoings
# ---------------------------------------------------------------------------

RATES_COUNCIL_AUTHORITY = "rates.council.authority_name"
RATES_COUNCIL_ANNUAL = "rates.council.annual_amount"
RATES_WATER_AUTHORITY = "rates.water.authority_name"
RATES_WATER_ANNUAL = "rates.water.annual_amount"
RATES_LAND_TAX_PAYABLE = "rates.land_tax.payable"
RATES_VACANT_LAND_TAX_PAYABLE = "rates.land_tax.vacant_residential_payable"
RATES_OWNERS_CORPORATION = "rates.owners_corporation.exists"

# Water authority certificate (Yarra Valley Water etc.)
RATES_WATER_CERTIFICATE_NUMBER = "rates.water.certificate_number"
RATES_WATER_ACCOUNT_NUMBER = "rates.water.account_number"
RATES_WATER_PROPERTY_NUMBER = "rates.water.property_number"
RATES_WATER_TOTAL_OUTSTANDING = "rates.water.total_outstanding"
RATES_WATER_PERIOD_FROM = "rates.water.period_from"
RATES_WATER_PERIOD_TO = "rates.water.period_to"
RATES_WATER_PROPERTY_TYPE = "rates.water.property_type"

def water_charge(index: int, field: str) -> str:
    """Return e.g. 'rates.water.charges.0.label'."""
    return f"rates.water.charges.{index}.{field}"

WATER_CHARGE_FIELDS = ("label", "period", "amount", "outstanding")
RATES_WATER_CHARGE_COUNT = "rates.water.charges.count"

# Land tax certificate (SRO)
RATES_LAND_TAX_AMOUNT = "rates.land_tax.amount"
RATES_LAND_TAX_TAXABLE_VALUE = "rates.land_tax.taxable_value"
RATES_LAND_TAX_CAPITAL_IMPROVED_VALUE = "rates.land_tax.capital_improved_value"
RATES_LAND_TAX_SITE_VALUE = "rates.land_tax.site_value"
RATES_LAND_TAX_EXEMPTION_REASON = "rates.land_tax.exemption_reason"
RATES_LAND_TAX_CERTIFICATE_NUMBER = "rates.land_tax.certificate_number"
RATES_LAND_TAX_COMMISSIONER = "rates.land_tax.commissioner"
RATES_LAND_TAX_ASSESSED_YEAR = "rates.land_tax.assessed_year"
RATES_LAND_TAX_VENDOR_NAME = "rates.land_tax.vendor_name"
RATES_VACANT_LAND_TAX_AMOUNT = "rates.land_tax.vacant_residential_amount"

# ---------------------------------------------------------------------------
# Planning + building
# ---------------------------------------------------------------------------

PLANNING_BUSHFIRE_PRONE = "planning.bushfire_prone"
PLANNING_OVERLAYS_EXIST = "planning.overlays_exist"
PLANNING_GAIC_TRIGGER = "planning.gaic_trigger"
PLANNING_PERMITS_ISSUED_OR_REFUSED = "planning.permits_issued_or_refused"

PROPERTY_ROAD_ACCESS = "property.road_access"

BUILDING_PERMITS_LAST_7_YEARS = "building.permits_last_7_years"
BUILDING_WORKS_LAST_7_YEARS = "building.works_last_7_years"
BUILDING_WORKS_WITHOUT_PERMIT = "building.works_without_permit"
BUILDING_WORKS_WITHOUT_APPROVAL = "building.works_without_approval"
BUILDING_IMPROVED_PAST_6_5_YEARS = "building.improved_past_6_5_years"
BUILDING_NEW_APPLIANCES_GAS_ELEC = "building.new_appliances_gas_elec_plumbing"
BUILDING_COMPLIANCE_CERTIFICATES = "building.compliance_certificates_obtained"
BUILDING_PROPERTY_LESS_THAN_7_YEARS = "building.property_less_than_7_years_old"
BUILDING_HAS_WATER_TANK = "building.has_water_tank"
BUILDING_PERMIT_REQUIRED_WATER_TANK = "building.permit_required_for_water_tank"
BUILDING_RECENT_PERMITS_EXIST = "building.recent_permits_exist"
BUILDING_RECENT_PERMITS_COUNT = "building.recent_permits.count"


def building_permit(index: int, field: str) -> str:
    """Return e.g. 'building.permit.0.number'."""
    return f"building.permit.{index}.{field}"


BUILDING_PERMIT_FIELDS = ("kind", "number", "issue_date", "description", "within_last_7_years")

# ---------------------------------------------------------------------------
# Insurance (vendor disclosure)
# ---------------------------------------------------------------------------

INSURANCE_HOME_DISCLOSED_TEXT = "insurance.home.vendor_disclosed_text"

# ---------------------------------------------------------------------------
# Planning (authoritative: VicPlan / Section 199 Planning Certificate)
# ---------------------------------------------------------------------------
PLANNING_ZONE = "planning.zone"
PLANNING_SCHEME = "planning.scheme"
PLANNING_RESPONSIBLE_AUTHORITY = "planning.responsible_authority"
PLANNING_OVERLAY_NAMES = "planning.overlay_names"     # comma-separated string
PLANNING_OVERLAYS_LIST = "planning.overlays_list"     # list[str]

# Sale details (vendor form)
SALE_IS_AUCTION = "sale.is_auction"
SALE_ESTIMATED_PRICE = "sale.estimated_price"
SALE_IS_TERMS_CONTRACT = "sale.is_terms_contract"
SALE_HAS_MORTGAGE = "sale.has_mortgage"
SALE_S27_EARLY_RELEASE = "sale.s27_early_release"
"""True if vendor wants early release of deposit under Section 27."""

# Insurance (Certificate of Currency)
INSURANCE_HOME_COMPANY = "insurance.home.company"
INSURANCE_HOME_POLICY_NUMBER = "insurance.home.policy_number"
INSURANCE_HOME_POLICY_TYPE = "insurance.home.policy_type"
INSURANCE_HOME_AMOUNT_INSURED = "insurance.home.amount_insured"
INSURANCE_HOME_EXPIRY_DATE = "insurance.home.expiry_date"

# Rates: land tax authority (always SRO)
RATES_LAND_TAX_AUTHORITY_NAME = "rates.land_tax.authority_name"

# ---------------------------------------------------------------------------
# Title (Victorian Register Search Statement)
# ---------------------------------------------------------------------------
#
# Single-instance scalar fields live under title.*. Multi-instance entities
# (proprietors, encumbrances) use indexed sub-paths.

TITLE_VOLUME_FOLIO = "title.volume_folio"
TITLE_VOLUME = "title.volume"
TITLE_FOLIO = "title.folio"
TITLE_SECURITY_NUMBER = "title.security_number"
TITLE_PRODUCED_AT = "title.produced_at"

TITLE_LAND_DESCRIPTION = "title.land_description"
TITLE_PARENT_TITLE = "title.parent_title"
TITLE_PARENT_VOLUME = "title.parent_volume"
TITLE_PARENT_FOLIO = "title.parent_folio"
TITLE_CREATED_BY_INSTRUMENT = "title.created_by_instrument"
TITLE_CREATED_AT = "title.created_at"

TITLE_PLAN_TYPE = "title.plan_type"
TITLE_PLAN_NUMBER = "title.plan_number"
TITLE_LOT_NUMBER = "title.lot_number"

TITLE_ESTATE = "title.estate"
TITLE_TENANCY = "title.tenancy"

TITLE_PROPRIETOR_COUNT = "title.proprietor.count"


def title_proprietor(index: int, field: str) -> str:
    """Return e.g. 'title.proprietor.0.name'."""
    return f"title.proprietor.{index}.{field}"


PROPRIETOR_FIELDS = ("name", "address", "dealing", "dealing_date")

TITLE_ENCUMBRANCE_COUNT = "title.encumbrance.count"


def title_encumbrance(index: int, field: str) -> str:
    """Return e.g. 'title.encumbrance.0.type'."""
    return f"title.encumbrance.{index}.{field}"


ENCUMBRANCE_FIELDS = ("type", "number", "date", "party", "text")

TITLE_HAS_MORTGAGE = "title.has_mortgage"
TITLE_HAS_COVENANT = "title.has_covenant"
TITLE_HAS_CAVEAT = "title.has_caveat"
TITLE_HAS_NOTICE = "title.has_notice"
TITLE_HAS_SECTION_173_AGREEMENT = "title.has_section_173_agreement"

TITLE_DIAGRAM_LOCATION = "title.diagram_location"
TITLE_ECT_CONTROLLER = "title.ect_controller"
TITLE_ECT_EFFECTIVE_FROM = "title.ect_effective_from"
TITLE_STREET_ADDRESS = "title.street_address"

# ---------------------------------------------------------------------------
# Rates: water certificate annualisation
# ---------------------------------------------------------------------------

RATES_WATER_CERT_AMOUNT_ANNUAL = "rates.water.cert_amount_annual"
"""Quarterly water cert amount × 4 — emitted at confidence 0.75 so vendor form wins."""
RATES_WATER_CERT_PERIOD_TYPE = "rates.water.cert_period_type"
"""e.g. 'quarterly' — set by the water cert extractor based on charge periods."""

# ---------------------------------------------------------------------------
# Rates: owners corporation fees
# ---------------------------------------------------------------------------

RATES_OC_AUTHORITY_NAME = "rates.owners_corporation.authority_name"
"""Name of the OC management body / body corporate."""
RATES_OC_ANNUAL_AMOUNT = "rates.owners_corporation.annual_amount"
"""Annual OC levy / fee in dollars (string or float)."""

# ---------------------------------------------------------------------------
# Document dates — used by the attachment list builder
# ---------------------------------------------------------------------------

DOCS_WATER_CERT_DATE = "docs.water_cert.date"
DOCS_LAND_TAX_CERT_DATE = "docs.land_tax_cert.date"
DOCS_VIC_TITLE_SEARCH_DATE = "docs.vic_title.search_date"
DOCS_PLANNING_CERT_DATE = "docs.planning_cert.date"
DOCS_VICROADS_CERT_DATE = "docs.vicroads_cert.date"
DOCS_EPA_CERT_DATE = "docs.epa_cert.date"
DOCS_PROPERTY_REPORT_DATE = "docs.property_report.date"
DOCS_OC_CERT_DATE = "docs.oc_cert.date"
DOCS_OC_BASIC_REPORT_DATE = "docs.oc_basic_report.date"
DOCS_COUNCIL_LAND_INFO_CERT_DATE = "docs.council_land_info_cert.date"
DOCS_COUNCIL_BUILDING_APPROVAL_CERT_DATE = "docs.council_building_approval_cert.date"
DOCS_BLDG_WARRANTY_DATE = "docs.bldg_warranty.date"
DOCS_OWNER_BUILDER_REPORT_DATE = "docs.owner_builder_report.date"
DOCS_RESIDENTIAL_TENANCY_AGREEMENT_DATE = "docs.residential_tenancy_agreement.date"

# Document plan identifiers (list of dicts stored as JSON strings)
DOCS_PLAN_OF_SUBDIVISION = "docs.plan_of_subdivision"
"""list[dict] — each item: {number: 'PS826454D', date: '02/04/2026'}"""
DOCS_PLAN_OF_CONSOLIDATION = "docs.plan_of_consolidation"
"""list[dict] — each item: {number: 'PC370662X', date: '11/03/2026'}"""
DOCS_COVENANT = "docs.covenant"
"""list[dict] — each item: {number: '1489008', date: '13/03/2026'}"""

# ---------------------------------------------------------------------------
# Policy computed and always-fixed facts
# ---------------------------------------------------------------------------

POLICY_ATTACHMENTS_TEXT = "policy.attachments.text"
"""Full formatted attachments list for Tab 6 field 13."""

POLICY_WATER_AMOUNT_VERIFIED = "policy.verification.water_amount_match"
"""True if vendor-form water rate ≈ cert quarterly × 4; False = discrepancy."""

# Always-True policy facts — emitted by policy/defaults.py
POLICY_TAB1_AMOUNTS_ARE_CHECKED = "policy.tab1.amounts_are_checked"
POLICY_TAB1_CERTS_ATTACHED = "policy.tab1.certificates_attached"
POLICY_TAB1_TOTAL_DOES_NOT_EXCEED = "policy.tab1.total_does_not_exceed"
POLICY_TAB1_TOTAL_DOES_NOT_EXCEED_AMOUNT = "policy.tab1.total_does_not_exceed_amount"
POLICY_TAB2_TITLE_IN_ATTACHED = "policy.tab2.title_in_attached"
POLICY_TAB2_FAILURE_CHECKED = "policy.tab2.failure_to_comply_checked"
POLICY_TAB2_FAILURE_TEXT = "policy.tab2.failure_to_comply_text"
POLICY_TAB2_PLANNING_CERT_ATTACHED = "policy.tab2.planning_cert_attached"
POLICY_TAB3_BUILDING_PERMITS_AS_FOLLOWS = "policy.tab3.building_permits_as_follows"
POLICY_TAB3_BUILDING_PERMITS_TEXT = "policy.tab3.building_permits_text"
POLICY_TAB6_DUE_DILIGENCE_TEXT = "policy.tab6.due_diligence_text"
