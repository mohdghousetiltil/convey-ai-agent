"""Brain A — canonical extractors that emit Fact objects.

Each extractor is a pure function:

    extract_<doc_type>_facts(doc: Document) -> list[Fact]

Facts use the canonical fact path namespace defined in
`canonical/extractors/paths.py`. Every Fact carries a verbatim quote
from the document text so the FactStore can verify it later.
"""
from triconvey_agent.canonical.extractors.planning_certificate import (
    EXTRACTOR_NAME as PLANNING_EXTRACTOR_NAME,
    extract_planning_certificate_facts,
)
from triconvey_agent.canonical.extractors.land_tax_certificate import (
    EXTRACTOR_NAME as LAND_TAX_EXTRACTOR_NAME,
    extract_land_tax_certificate_facts,
)
from triconvey_agent.canonical.extractors.vendor_form import (
    EXTRACTOR_NAME as VENDOR_FORM_EXTRACTOR_NAME,
    extract_vendor_form_facts,
)
from triconvey_agent.canonical.extractors.vic_title import (
    EXTRACTOR_NAME as VIC_TITLE_EXTRACTOR_NAME,
    extract_vic_title_facts,
)
from triconvey_agent.canonical.extractors.water_authority_certificate import (
    EXTRACTOR_NAME as WATER_AUTHORITY_EXTRACTOR_NAME,
    extract_water_authority_certificate_facts,
)
from triconvey_agent.canonical.extractors.generic_doc_meta import (
    EXTRACTOR_NAME as GENERIC_META_EXTRACTOR_NAME,
    extract_generic_doc_meta_facts,
)

__all__ = [
    "extract_vendor_form_facts",
    "VENDOR_FORM_EXTRACTOR_NAME",
    "extract_vic_title_facts",
    "VIC_TITLE_EXTRACTOR_NAME",
    "extract_water_authority_certificate_facts",
    "WATER_AUTHORITY_EXTRACTOR_NAME",
    "extract_land_tax_certificate_facts",
    "LAND_TAX_EXTRACTOR_NAME",
    "extract_planning_certificate_facts",
    "PLANNING_EXTRACTOR_NAME",
    "extract_generic_doc_meta_facts",
    "GENERIC_META_EXTRACTOR_NAME",
]
