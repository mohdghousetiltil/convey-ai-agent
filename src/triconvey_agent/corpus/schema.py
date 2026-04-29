"""Document corpus data structures.

Each document uploaded to a matter run is processed into a DocumentCorpusEntry.
All entries are merged into a MatterCorpus that is fed to Brain F before it answers.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Leaf types
# ---------------------------------------------------------------------------


@dataclass
class ExtractedDate:
    label: str
    value: str
    source_page: int | None = None


@dataclass
class ExtractedAmount:
    label: str
    value: str
    currency: str = "AUD"
    frequency: str | None = None  # "Annually", "Quarterly", "Monthly", "One-off"
    source_page: int | None = None


@dataclass
class ExtractedName:
    role: str   # "vendor", "purchaser", "solicitor", "agent", "owner", etc.
    name: str
    source_page: int | None = None


@dataclass
class ExtractedReference:
    label: str
    value: str
    source_page: int | None = None


@dataclass
class AuthorityEntry:
    name: str
    amount: str | None = None
    frequency: str | None = None   # "Annually", "Quarterly", "Monthly"
    authority_type: str | None = None  # "council", "water", "state_revenue", "land_tax", "owners_corporation"

    def display(self) -> str:
        """Format as: Name - Frequency  (matches required UI format)."""
        parts = [self.name]
        if self.frequency:
            parts.append(self.frequency.capitalize())
        elif self.authority_type == "state_revenue":
            parts.append("Land Tax")
        return " - ".join(parts)


@dataclass
class PermitInfo:
    number: str | None = None
    date: str | None = None
    description: str | None = None
    source_page: int | None = None


@dataclass
class OwnerCorporationInfo:
    name: str | None = None
    amount: str | None = None
    frequency: str | None = None
    is_inactive: bool | None = None
    source_page: int | None = None

    def display(self) -> str:
        parts = ["Owners Corporation Insurance"]
        if self.frequency:
            parts.append(self.frequency.capitalize())
        result = " – ".join(parts)
        if self.name:
            result += f", {self.name}"
        return result


@dataclass
class AttachmentEntry:
    name: str
    id: str | None = None
    date: str | None = None
    document_type: str | None = None


@dataclass
class ServiceEntry:
    service_type: str   # "mains_water", "mains_gas", "mains_sewerage", "telephone_nbn", etc.
    connected: bool
    notes: str | None = None


@dataclass
class QAPair:
    question: str
    answer: str
    source_page: int | None = None


# ---------------------------------------------------------------------------
# Per-document corpus entry
# ---------------------------------------------------------------------------


@dataclass
class DocumentCorpusEntry:
    document_id: str
    filename: str
    document_type: str
    page_count: int
    pages_processed: int  # may be capped at 20 for large docs

    # Core conveyancing identifiers
    client_name: str | None = None
    volume_folio: str | None = None
    property_address: str | None = None

    # Authorities (formatted for UI display)
    council_authority: AuthorityEntry | None = None
    water_authority: AuthorityEntry | None = None
    state_revenue: AuthorityEntry | None = None
    land_tax: AuthorityEntry | None = None
    owner_corporation: OwnerCorporationInfo | None = None

    # Permits
    building_permit: PermitInfo | None = None
    occupancy_permit: PermitInfo | None = None

    # Property features
    is_bushfire_prone: bool | None = None
    has_road_access: bool | None = None

    # Structured extracted data
    dates: list[ExtractedDate] = field(default_factory=list)
    amounts: list[ExtractedAmount] = field(default_factory=list)
    names: list[ExtractedName] = field(default_factory=list)
    reference_numbers: list[ExtractedReference] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    services: list[ServiceEntry] = field(default_factory=list)
    attachments: list[AttachmentEntry] = field(default_factory=list)
    important_information: list[str] = field(default_factory=list)
    qa_pairs: list[QAPair] = field(default_factory=list)

    extracted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentCorpusEntry:
        # Reconstruct nested dataclasses
        def _authority(d: dict | None) -> AuthorityEntry | None:
            return AuthorityEntry(**d) if d else None

        def _permit(d: dict | None) -> PermitInfo | None:
            return PermitInfo(**d) if d else None

        def _oc(d: dict | None) -> OwnerCorporationInfo | None:
            return OwnerCorporationInfo(**d) if d else None

        entry = cls(
            document_id=data["document_id"],
            filename=data["filename"],
            document_type=data["document_type"],
            page_count=data["page_count"],
            pages_processed=data["pages_processed"],
            client_name=data.get("client_name"),
            volume_folio=data.get("volume_folio"),
            property_address=data.get("property_address"),
            council_authority=_authority(data.get("council_authority")),
            water_authority=_authority(data.get("water_authority")),
            state_revenue=_authority(data.get("state_revenue")),
            land_tax=_authority(data.get("land_tax")),
            owner_corporation=_oc(data.get("owner_corporation")),
            building_permit=_permit(data.get("building_permit")),
            occupancy_permit=_permit(data.get("occupancy_permit")),
            is_bushfire_prone=data.get("is_bushfire_prone"),
            has_road_access=data.get("has_road_access"),
            dates=[ExtractedDate(**d) for d in data.get("dates", [])],
            amounts=[ExtractedAmount(**d) for d in data.get("amounts", [])],
            names=[ExtractedName(**d) for d in data.get("names", [])],
            reference_numbers=[ExtractedReference(**d) for d in data.get("reference_numbers", [])],
            descriptions=data.get("descriptions", []),
            services=[ServiceEntry(**d) for d in data.get("services", [])],
            attachments=[AttachmentEntry(**d) for d in data.get("attachments", [])],
            important_information=data.get("important_information", []),
            qa_pairs=[QAPair(**d) for d in data.get("qa_pairs", [])],
            extracted_at=data.get("extracted_at", ""),
        )
        return entry


# ---------------------------------------------------------------------------
# Matter corpus — all documents merged
# ---------------------------------------------------------------------------


@dataclass
class MatterCorpus:
    matter_id: str
    run_id: str
    documents: list[DocumentCorpusEntry] = field(default_factory=list)
    # Documents awaiting user confirmation before merging
    pending: list[DocumentCorpusEntry] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def confirm_pending(self, document_id: str) -> DocumentCorpusEntry | None:
        """Move a pending entry into confirmed documents. Returns the entry or None."""
        for i, entry in enumerate(self.pending):
            if entry.document_id == document_id:
                confirmed = self.pending.pop(i)
                self.documents.append(confirmed)
                self.touch()
                return confirmed
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matter_id": self.matter_id,
            "run_id": self.run_id,
            "documents": [d.to_dict() for d in self.documents],
            "pending": [d.to_dict() for d in self.pending],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatterCorpus:
        return cls(
            matter_id=data["matter_id"],
            run_id=data["run_id"],
            documents=[DocumentCorpusEntry.from_dict(d) for d in data.get("documents", [])],
            pending=[DocumentCorpusEntry.from_dict(d) for d in data.get("pending", [])],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def save(self, path: str) -> None:
        import pathlib
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> MatterCorpus:
        import pathlib
        p = pathlib.Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
