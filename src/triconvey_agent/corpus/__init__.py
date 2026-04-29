"""Document corpus — rich structured extraction from matter documents."""
from triconvey_agent.corpus.schema import (
    AttachmentEntry,
    AuthorityEntry,
    DocumentCorpusEntry,
    ExtractedAmount,
    ExtractedDate,
    ExtractedName,
    ExtractedReference,
    MatterCorpus,
    OwnerCorporationInfo,
    PermitInfo,
    QAPair,
    ServiceEntry,
)

__all__ = [
    "MatterCorpus",
    "DocumentCorpusEntry",
    "AuthorityEntry",
    "OwnerCorporationInfo",
    "PermitInfo",
    "AttachmentEntry",
    "ServiceEntry",
    "ExtractedDate",
    "ExtractedAmount",
    "ExtractedName",
    "ExtractedReference",
    "QAPair",
]
