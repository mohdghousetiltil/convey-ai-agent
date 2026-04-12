from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from triconvey_agent.catalog.sec32_from_yaml import collect_sec32_questions_from_docs
from triconvey_agent.extractors.planning_report import extract_planning_report_fields
from triconvey_agent.extractors.property_report import extract_property_report_fields
from triconvey_agent.extractors.vendor_form import extract_vendor_form_sections
from triconvey_agent.extractors.vic_title import extract_vic_title_fields
from triconvey_agent.extractors.yes_no import extract_yes_no_questions
from triconvey_agent.ai.reviewer import run_ai_review
from triconvey_agent.ai.document_extractor import extract_all_documents_with_ai
from triconvey_agent.ai.property_agent import run_property_agent
from triconvey_agent.ai.self_auditor import run_self_audit
from triconvey_agent.ai.universal_summarizer import summarise_all_documents
from triconvey_agent.ingest.pdf_loader import load_pdf_document
from triconvey_agent.ingest.yaml_loader import load_yaml_document
from triconvey_agent.output.fill_artifact_builder import build_fill_artifact
from triconvey_agent.quality.review_rules import build_quality_review_report
from triconvey_agent.resolve.merger import merge_sections
from triconvey_agent.resolve.reconciler import reconcile_extractions, reconcile_from_agent
from triconvey_agent.resolve.review import build_review_summary
from triconvey_agent.resolve.sec32_coverage import build_sec32_coverage
from triconvey_agent.resolve.section32_mapper import map_to_section32_questions
from triconvey_agent.resolve.title_selector import select_titles
from triconvey_agent.schemas.documents import Document
from triconvey_agent.schemas.extracted import FinalExtraction
from triconvey_agent.schemas.fill_artifact import FillArtifact
from triconvey_agent.transformers.narrative_fields import (
    enrich_improvement_details,
    mark_incomplete_appliance_details,
    parse_improvement_details,
    parse_new_appliance_details,
)
from triconvey_agent.transformers.parcel_parser import normalize_parcel_details, parse_parcel_details
from triconvey_agent.transformers.structured_fields import cleanup_zone_list, normalize_money_string, split_beneficiaries
from triconvey_agent.transformers.title_refs import parse_title_references
from triconvey_agent.transformers.utilities import normalize_utilities

SUPPORTED_SUFFIXES = {".pdf", ".yaml", ".yml"}


def collect_files(input_path: str | Path, recursive: bool = True) -> list[Path]:
    path = Path(input_path).expanduser().resolve()
    if path.is_file():
        return [path]

    iterator = path.rglob("*") if recursive else path.iterdir()
    return [child for child in sorted(iterator) if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES]


def load_document(path: Path) -> Document:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf_document(path)
    if suffix in {".yaml", ".yml"}:
        return load_yaml_document(path)
    raise ValueError(f"Unsupported file type: {path}")


def extract_document(doc: Document) -> dict[str, Any]:
    if doc.document_type.value == "vendor_form":
        payload = extract_vendor_form_sections(doc)
        payload["section32_questions"] = extract_yes_no_questions(doc)
        return payload

    if doc.document_type.value == "vic_title":
        return extract_vic_title_fields(doc)

    if doc.document_type.value == "planning_report":
        return extract_planning_report_fields(doc)

    if doc.document_type.value == "property_report":
        return extract_property_report_fields(doc)

    return {}


def _derive_preferred_volume_folio(merged) -> str | None:
    """Build preferred_volume_folio from the vendor form's volume/folio fields.

    The vendor form extracts volume_or_book and folio_or_number separately.
    We combine them to match the format used in VIC title documents, e.g.
    "VOLUME 11155 FOLIO 874", so select_titles() can prefer the right title
    without any client-specific hardcoding.
    """
    vol_field = merged.property_details.get("volume_or_book")
    fol_field = merged.property_details.get("folio_or_number")
    vol = vol_field.value if vol_field and hasattr(vol_field, "value") else None
    fol = fol_field.value if fol_field and hasattr(fol_field, "value") else None
    if vol and fol:
        return f"VOLUME {str(vol).strip()} FOLIO {str(fol).strip()}".upper()
    return None


def post_process_extraction(merged):
    if "beneficiaries" in merged.trustee:
        merged.trustee["beneficiaries"] = split_beneficiaries(merged.trustee["beneficiaries"])

    if "parcel_details_raw" in merged.property_details:
        merged.property_details["parcel_details_raw"] = parse_parcel_details(
            merged.property_details["parcel_details_raw"]
        )
        merged.property_details["parcel_details_raw"] = normalize_parcel_details(
            merged.property_details["parcel_details_raw"]
        )

    if "volume_or_book" in merged.property_details:
        merged.property_details["volume_or_book"] = parse_title_references(
            merged.property_details["volume_or_book"]
        )

    if "new_appliance_details" in merged.planning_building_permits:
        merged.planning_building_permits["new_appliance_details"] = parse_new_appliance_details(
            merged.planning_building_permits["new_appliance_details"]
        )
        merged.planning_building_permits["new_appliance_details"] = mark_incomplete_appliance_details(
            merged.planning_building_permits["new_appliance_details"]
        )

    if "improvement_details" in merged.planning_building_permits:
        merged.planning_building_permits["improvement_details"] = parse_improvement_details(
            merged.planning_building_permits["improvement_details"]
        )
        merged.planning_building_permits["improvement_details"] = enrich_improvement_details(
            merged.planning_building_permits["improvement_details"]
        )

    if "planning_zones" in merged.planning_building_permits:
        merged.planning_building_permits["planning_zones"] = cleanup_zone_list(
            merged.planning_building_permits["planning_zones"]
        )

    if "utilities" in merged.services_connected:
        merged.services_connected["utilities"] = normalize_utilities(
            merged.services_connected["utilities"]
        )

    if "council_rates" in merged.rates_taxes_charges:
        merged.rates_taxes_charges["council_rates"] = normalize_money_string(
            merged.rates_taxes_charges["council_rates"]
        )

    return merged


def _process_documents(docs: list[Document], ai_client=None, use_ai_extract: bool = False, agent_mode: bool = False):
    docs = sorted(docs, key=lambda doc: doc.filename.lower())

    extracted_by_doc: list[tuple[Document, dict[str, Any]]] = []
    for doc in docs:
        payload = extract_document(doc)
        if payload:
            extracted_by_doc.append((doc, payload))

    merged = merge_sections(extracted_by_doc)
    merged = post_process_extraction(merged)

    # --- Universal document summariser (Step A-2) ---
    # Runs GPT-4o on EVERY PDF — including those the rules classifier missed.
    # Each summary carries: document_category, display_name, issuing_authority,
    # document_date (from actual document, not inferred), key_facts, is_attachment.
    # Results stored in merged.source_documents and used by normalized_builder
    # to build the correct attachment list with verified dates.
    if agent_mode and ai_client is not None:
        # ---------------------------------------------------------------
        # AGENT MODE (Phase 3+4): unified multi-doc AI + self-audit
        # ---------------------------------------------------------------
        # Step 1: send ALL documents to GPT-4o in one call — full context
        kb = run_property_agent(docs, ai_client)
        # Step 2: self-audit — re-check uncertain fields against source text
        kb = run_self_audit(kb, docs, ai_client)
        # Step 3: populate source_documents from the agent's document list
        merged.source_documents = kb.as_source_documents()
        # Step 4: merge agent knowledge into rules-based extraction
        merged = reconcile_from_agent(merged, kb.raw)
        # Step 5: also store the full agent summary for CLI display
        merged.review_summary["agent_summary"] = kb.as_agent_summary()

    elif use_ai_extract and ai_client is not None:
        # ---------------------------------------------------------------
        # AI-EXTRACT MODE (Phase 2): per-doc grounded extraction
        # ---------------------------------------------------------------
        source_docs = summarise_all_documents(docs, ai_client)
        merged.source_documents = source_docs

        # Grounded field extraction + reconciliation (Step A-3)
        ai_results = extract_all_documents_with_ai(docs, ai_client)
        merged = reconcile_extractions(merged, ai_results)
    merged.section32_questions = map_to_section32_questions(merged)
    merged.skipped_sections = [
        "Emergency Contact",
        "Real Estate Agent",
        "Acknowledgment",
    ]
    title_selection = select_titles(
        extracted_by_doc,
        preferred_volume_folio=_derive_preferred_volume_folio(merged),
    )

    if title_selection.primary_title:
        merged.vic_title_extract = title_selection.primary_title.fields
    merged.additional_titles = [record.model_dump(mode="json") for record in title_selection.additional_titles]
    discovered_sec32 = collect_sec32_questions_from_docs(docs)
    coverage = build_sec32_coverage(discovered_sec32)
    merged.source_summary = {
        "documents_processed": len(docs),
        "document_types": [doc.document_type.value for doc in docs],
        "files": [doc.filename for doc in docs],
        "sec32_yaml_files": [doc.filename for doc in docs if doc.document_type.value == "sec32_tab"],
        "primary_title_source": title_selection.primary_title.source_file if title_selection.primary_title else None,
        "additional_title_sources": [record.source_file for record in title_selection.additional_titles],
        **coverage,
    }
    if not merged.source_summary["sec32_yaml_files"]:
        merged.source_summary["sec32_yaml_status"] = "No Sec 32 YAML tabs were present in the input scope."
    merged.review_summary = build_review_summary(merged).model_dump(mode="json")
    quality_report = build_quality_review_report(merged)
    merged.review_summary = {
        **merged.review_summary,
        "quality_review_items": [item.model_dump(mode="json") for item in quality_report.items],
    }
    if ai_client is not None and quality_report.items:
        merged.ai_suggestions = run_ai_review(merged, quality_report, ai_client)
    else:
        merged.ai_suggestions = []
    return merged, docs


def run_pipeline_from_paths(
    paths: Iterable[str | Path],
    ai_client=None,
    use_ai_extract: bool = False,
    agent_mode: bool = False,
):
    docs = [load_document(Path(path)) for path in paths]
    merged, _ = _process_documents(docs, ai_client=ai_client, use_ai_extract=use_ai_extract, agent_mode=agent_mode)
    return merged


def run_pipeline_with_artifacts_from_paths(
    paths: Iterable[str | Path],
    ai_client=None,
    use_ai_extract: bool = False,
    agent_mode: bool = False,
) -> tuple[FinalExtraction, FillArtifact]:
    docs = [load_document(Path(path)) for path in paths]
    merged, processed_docs = _process_documents(
        docs, ai_client=ai_client, use_ai_extract=use_ai_extract, agent_mode=agent_mode
    )
    fill_artifact = build_fill_artifact(merged, processed_docs)
    return merged, fill_artifact


def run_pipeline(
    input_path: str | Path,
    recursive: bool = True,
    ai_client=None,
    use_ai_extract: bool = False,
    agent_mode: bool = False,
):
    files = collect_files(input_path, recursive=recursive)
    return run_pipeline_from_paths(files, ai_client=ai_client, use_ai_extract=use_ai_extract, agent_mode=agent_mode)


def run_pipeline_with_artifacts(
    input_path: str | Path,
    recursive: bool = True,
    ai_client=None,
    use_ai_extract: bool = False,
    agent_mode: bool = False,
):
    files = collect_files(input_path, recursive=recursive)
    return run_pipeline_with_artifacts_from_paths(
        files, ai_client=ai_client, use_ai_extract=use_ai_extract, agent_mode=agent_mode
    )
