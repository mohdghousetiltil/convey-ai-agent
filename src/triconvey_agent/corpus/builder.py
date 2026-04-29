"""Matter corpus builder.

Loads, saves, and builds a MatterCorpus for a run directory.
Handles incremental document processing with user-confirmation gating.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from triconvey_agent.corpus.schema import DocumentCorpusEntry, MatterCorpus

LOG = logging.getLogger(__name__)


def corpus_path(run_dir: Path) -> Path:
    return run_dir / "corpus.json"


def load_corpus(run_dir: Path, matter_id: str, run_id: str) -> MatterCorpus:
    """Load corpus from disk, creating an empty one if it does not exist."""
    path = corpus_path(run_dir)
    if path.exists():
        try:
            return MatterCorpus.load(str(path))
        except Exception as exc:
            LOG.warning("Could not load corpus at %s: %s — starting fresh", path, exc)
    return MatterCorpus(matter_id=matter_id, run_id=run_id)


def save_corpus(corpus: MatterCorpus, run_dir: Path) -> None:
    corpus.save(str(corpus_path(run_dir)))


def add_pending(corpus: MatterCorpus, entry: DocumentCorpusEntry, run_dir: Path) -> None:
    """Add a newly extracted entry to pending (awaiting user confirmation)."""
    # Remove any existing pending entry for the same document
    corpus.pending = [p for p in corpus.pending if p.document_id != entry.document_id]
    corpus.pending.append(entry)
    corpus.touch()
    save_corpus(corpus, run_dir)


def confirm_document(corpus: MatterCorpus, document_id: str, run_dir: Path) -> DocumentCorpusEntry | None:
    """Move a pending entry to confirmed. Returns the confirmed entry or None."""
    confirmed = corpus.confirm_pending(document_id)
    if confirmed:
        save_corpus(corpus, run_dir)
    return confirmed


def confirm_all_pending(corpus: MatterCorpus, run_dir: Path) -> list[DocumentCorpusEntry]:
    """Confirm all pending entries at once."""
    confirmed = []
    for entry in list(corpus.pending):
        c = corpus.confirm_pending(entry.document_id)
        if c:
            confirmed.append(c)
    save_corpus(corpus, run_dir)
    return confirmed


def build_corpus_context(corpus: MatterCorpus) -> str:
    """Build a structured text block to inject into Brain F system prompt.

    Only confirmed (non-pending) documents are included.
    """
    if not corpus.documents:
        return ""

    lines: list[str] = [
        "=== MATTER DOCUMENT CORPUS ===",
        f"Matter: {corpus.matter_id}  |  Run: {corpus.run_id}",
        f"Documents processed: {len(corpus.documents)}",
        "",
    ]

    # ---- Merged authority view ----
    authorities = _merge_authorities(corpus)
    if authorities:
        lines.append("AUTHORITIES AND RATES:")
        for a in authorities:
            lines.append(f"  • {a}")
        lines.append("")

    # ---- Per-document detail ----
    for doc in corpus.documents:
        lines.append(f"--- Document: {doc.filename} ({doc.document_type}) ---")
        if doc.pages_processed < doc.page_count:
            lines.append(f"  [Note: {doc.page_count} pages total; first {doc.pages_processed} pages processed]")

        if doc.client_name:
            lines.append(f"  Client: {doc.client_name}")
        if doc.volume_folio:
            lines.append(f"  Volume/Folio: {doc.volume_folio}")
        if doc.property_address:
            lines.append(f"  Property: {doc.property_address}")

        if doc.council_authority:
            lines.append(f"  Council: {doc.council_authority.name} | Amount: {doc.council_authority.amount or 'N/A'} | {doc.council_authority.frequency or ''}")
        if doc.water_authority:
            lines.append(f"  Water: {doc.water_authority.name} | Amount: {doc.water_authority.amount or 'N/A'} | {doc.water_authority.frequency or ''}")
        if doc.state_revenue:
            lines.append(f"  State Revenue: {doc.state_revenue.name} | Amount: {doc.state_revenue.amount or 'N/A'}")
        if doc.land_tax:
            lines.append(f"  Land Tax: {doc.land_tax.amount or 'N/A'}")
        if doc.owner_corporation:
            oc = doc.owner_corporation
            status = " [INACTIVE]" if oc.is_inactive else ""
            lines.append(f"  Owner Corporation: {oc.name or 'Present'}{status} | Amount: {oc.amount or 'N/A'} | {oc.frequency or ''}")

        if doc.building_permit:
            bp = doc.building_permit
            lines.append(f"  Building Permit: No. {bp.number or 'N/A'} | Date: {bp.date or 'N/A'}")
            if bp.description:
                lines.append(f"    Description: {bp.description}")
        if doc.occupancy_permit:
            op = doc.occupancy_permit
            lines.append(f"  Occupancy Permit: No. {op.number or 'N/A'} | Date: {op.date or 'N/A'}")

        if doc.is_bushfire_prone is not None:
            lines.append(f"  Bushfire Prone Area: {'YES' if doc.is_bushfire_prone else 'No'}")
        if doc.has_road_access is not None:
            lines.append(f"  Road Access: {'Yes' if doc.has_road_access else 'NO — CHECK REQUIRED'}")

        if doc.services:
            connected = [s.service_type for s in doc.services if s.connected]
            not_connected = [s.service_type for s in doc.services if not s.connected]
            if connected:
                lines.append(f"  Services connected: {', '.join(connected)}")
            if not_connected:
                lines.append(f"  Services NOT connected: {', '.join(not_connected)}")

        if doc.attachments:
            deduped = _deduplicate_attachments(doc.attachments)
            lines.append(f"  Attachments ({len(deduped)}):")
            for att in deduped:
                att_line = f"    - {att.name}"
                if att.id:
                    att_line += f" [ID: {att.id}]"
                if att.date:
                    att_line += f" ({att.date})"
                lines.append(att_line)

        if doc.names:
            for n in doc.names:
                lines.append(f"  {n.role.title()}: {n.name}")

        if doc.dates:
            lines.append("  Key dates:")
            for d in doc.dates[:8]:
                lines.append(f"    {d.label}: {d.value}")

        if doc.amounts:
            lines.append("  Key amounts:")
            for a in doc.amounts[:8]:
                freq = f" ({a.frequency})" if a.frequency else ""
                lines.append(f"    {a.label}: {a.value}{freq}")

        if doc.reference_numbers:
            for r in doc.reference_numbers[:6]:
                lines.append(f"  {r.label}: {r.value}")

        if doc.important_information:
            lines.append("  Important information:")
            for info in doc.important_information:
                lines.append(f"    ⚠ {info}")

        if doc.qa_pairs:
            lines.append("  Q&A from document:")
            for qa in doc.qa_pairs[:10]:
                lines.append(f"    Q: {qa.question}")
                lines.append(f"    A: {qa.answer}")

        if doc.descriptions:
            lines.append("  Descriptions:")
            for desc in doc.descriptions[:5]:
                lines.append(f"    {desc}")

        lines.append("")

    lines.append("=== END DOCUMENT CORPUS ===")
    return "\n".join(lines)


def _deduplicate_attachments(attachments: list) -> list:
    """Remove duplicate attachments where one name is a substring of another.

    Example: "Plan of Subdivision 009777" and "Plan of Subdivision LP009777"
    → keep only "Plan of Subdivision LP009777" (the longer, more complete name).

    Strategy:
    1. Normalise names to lowercase for comparison.
    2. For each pair, if name A is contained within name B, discard A.
    3. Where names are otherwise identical modulo whitespace, keep the longer one.
    """
    if not attachments:
        return []

    # Sort longest-first so the first surviving match wins
    sorted_atts = sorted(attachments, key=lambda a: len(a.name), reverse=True)
    kept: list = []

    for candidate in sorted_atts:
        c_norm = candidate.name.lower().strip()
        # Discard if any already-kept attachment's name contains this one
        dominated = any(
            c_norm in k.name.lower().strip() or k.name.lower().strip() in c_norm
            and len(k.name) > len(candidate.name)
            for k in kept
        )
        if not dominated:
            kept.append(candidate)

    # Restore original order (by first appearance)
    original_order = {a.name: i for i, a in enumerate(attachments)}
    kept.sort(key=lambda a: original_order.get(a.name, 999))
    return kept


def _merge_authorities(corpus: MatterCorpus) -> list[str]:
    """Produce a deduplicated authority display list in the required format:
      City of Monash - Annually
      Yarra Valley Water - Annually
      State Revenue Office - Land Tax
      Owners Corporation Insurance – Annually, OC Name
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(display: str) -> None:
        key = display.lower()
        if key not in seen:
            seen.add(key)
            result.append(display)

    for doc in corpus.documents:
        if doc.council_authority:
            _add(doc.council_authority.display())
        if doc.water_authority:
            _add(doc.water_authority.display())
        if doc.state_revenue:
            entry = doc.state_revenue
            entry.authority_type = "state_revenue"
            _add(entry.display())
        if doc.land_tax and doc.land_tax.name:
            _add(f"{doc.land_tax.name} - Land Tax")
        if doc.owner_corporation:
            _add(doc.owner_corporation.display())

    return result


def pending_summary(corpus: MatterCorpus) -> list[dict[str, Any]]:
    """Return a UI-friendly summary of pending entries for the chat confirmation card."""
    summaries = []
    for entry in corpus.pending:
        summaries.append({
            "document_id": entry.document_id,
            "filename": entry.filename,
            "document_type": entry.document_type,
            "page_count": entry.page_count,
            "pages_processed": entry.pages_processed,
            "highlights": _entry_highlights(entry),
        })
    return summaries


def _entry_highlights(entry: DocumentCorpusEntry) -> list[str]:
    """Key facts extracted — shown to user before they confirm."""
    h: list[str] = []
    if entry.client_name:
        h.append(f"Client: {entry.client_name}")
    if entry.volume_folio:
        h.append(f"Volume/Folio: {entry.volume_folio}")
    if entry.property_address:
        h.append(f"Property: {entry.property_address}")
    if entry.council_authority:
        h.append(f"Council: {entry.council_authority.name} ({entry.council_authority.amount or 'amount unknown'})")
    if entry.water_authority:
        h.append(f"Water: {entry.water_authority.name} ({entry.water_authority.amount or 'amount unknown'})")
    if entry.land_tax and entry.land_tax.amount:
        h.append(f"Land Tax: {entry.land_tax.amount}")
    if entry.owner_corporation:
        oc = entry.owner_corporation
        status = " [INACTIVE]" if oc.is_inactive else ""
        h.append(f"Owner Corporation: {oc.name or 'Present'}{status}")
    if entry.building_permit:
        h.append(f"Building Permit: {entry.building_permit.number or '?'} ({entry.building_permit.date or 'date unknown'})")
    if entry.occupancy_permit:
        h.append(f"Occupancy Permit: {entry.occupancy_permit.number or '?'} ({entry.occupancy_permit.date or 'date unknown'})")
    if entry.is_bushfire_prone is True:
        h.append("⚠ Bushfire Prone Area: YES")
    if entry.has_road_access is False:
        h.append("⚠ Road Access: NOT CONFIRMED")
    if entry.attachments:
        h.append(f"Attachments: {len(entry.attachments)} found")
    if entry.important_information:
        h.append(f"Important notes: {len(entry.important_information)} item(s)")
    return h
