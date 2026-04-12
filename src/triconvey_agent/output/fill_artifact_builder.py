from __future__ import annotations

from typing import Any

from triconvey_agent.schemas.documents import Document
from triconvey_agent.schemas.extracted import Evidence, FieldValue, FinalExtraction
from triconvey_agent.schemas.fill_artifact import (
    ArtifactEvidence,
    ArtifactFact,
    FillArtifact,
    QuestionTarget,
)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _to_evidence(evidence_item: Evidence) -> ArtifactEvidence:
    span = evidence_item.span
    return ArtifactEvidence(
        source_file=evidence_item.source_file,
        source_type=evidence_item.source_type.value if evidence_item.source_type else None,
        page=span.page if span else None,
        section=span.section if span else None,
        line_start=span.line_start if span else None,
        line_end=span.line_end if span else None,
        label=evidence_item.label,
        snippet=evidence_item.snippet,
    )


def _fieldvalue_to_fact(category: str, key: str, field: FieldValue) -> ArtifactFact:
    status = "supported"
    review_notes: list[str] = []

    if field.conflicts:
        status = "conflicted"
    elif field.requires_review:
        status = "incomplete"
    elif field.value is None:
        status = "unanswered"

    if isinstance(field.value, dict) and field.value.get("is_incomplete") is True:
        status = "incomplete"
        review_notes.append("Parsed value marked incomplete.")

    return ArtifactFact(
        fact_id=f"{category}.{key}",
        canonical_key=field.normalized_key or key,
        category=category,
        value=field.value,
        value_type=_value_type(field.value),
        confidence=field.confidence,
        status=status,
        normalized_from=key,
        source_extractors=[field.extractor] if field.extractor else [],
        evidence=[_to_evidence(item) for item in field.evidence],
        conflicts=[item.model_dump(mode="json") for item in field.conflicts],
        review_notes=review_notes,
    )


def _flatten_facts(extraction: FinalExtraction) -> list[ArtifactFact]:
    facts: list[ArtifactFact] = []
    section_map = {
        "vendor_core": extraction.vendor_core,
        "trustee": extraction.trustee,
        "property_details": extraction.property_details,
        "services_connected": extraction.services_connected,
        "rates_taxes_charges": extraction.rates_taxes_charges,
        "planning_building_permits": extraction.planning_building_permits,
        "vic_title_extract": extraction.vic_title_extract,
    }

    for category, payload in section_map.items():
        for key, field in payload.items():
            facts.append(_fieldvalue_to_fact(category, key, field))

    return facts


def _documents_summary(docs: list[Document]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, doc in enumerate(docs, start=1):
        output.append(
            {
                "document_id": f"doc_{index}",
                "filename": doc.filename,
                "file_type": doc.file_type.value,
                "document_type": doc.document_type.value,
                "classification_confidence": doc.classification_confidence,
                "yaml_tab_name": doc.yaml_tab_name,
                "yaml_total_fields": doc.yaml_total_fields,
            }
        )
    return output


def _build_fact_index(facts: list[ArtifactFact]) -> dict[str, list[ArtifactFact]]:
    index: dict[str, list[ArtifactFact]] = {}
    for fact in facts:
        index.setdefault(fact.canonical_key, []).append(fact)
    return index


QUESTION_RULES: list[dict[str, Any]] = [
    {
        "match_any": ["bushfire prone area", "designated bushfire prone area"],
        "canonical_keys": ["bushfire_prone_area"],
        "answer_type": "boolean_true_means_checked",
    },
    {
        "match_any": ["road access", "no road access"],
        "canonical_keys": ["road_access"],
        "answer_type": "boolean_negative_checkbox",
    },
    {
        "match_any": ["planning scheme"],
        "canonical_keys": ["planning_scheme"],
        "answer_type": "text",
    },
    {
        "match_any": ["planning overlay", "name of planning overlay"],
        "canonical_keys": ["planning_overlays_affecting_land", "planning_overlays"],
        "answer_type": "list_or_boolean",
    },
    {
        "match_any": ["owners corporation"],
        "canonical_keys": ["owners_corporation"],
        "answer_type": "boolean_attachment_style",
    },
    {
        "match_any": ["gaic", "growth areas infrastructure contribution"],
        "canonical_keys": ["gaic_trigger"],
        "answer_type": "boolean",
    },
    {
        "match_any": ["electricity supply"],
        "canonical_keys": ["mains_electricity"],
        "answer_type": "service_not_connected_checkbox",
    },
    {
        "match_any": ["gas supply"],
        "canonical_keys": ["bottled_gas"],
        "answer_type": "service_not_connected_checkbox",
    },
    {
        "match_any": ["water supply"],
        "canonical_keys": ["tank_water", "utilities"],
        "answer_type": "service_not_connected_checkbox",
    },
    {
        "match_any": ["sewerage"],
        "canonical_keys": ["septic_tank", "utilities"],
        "answer_type": "service_not_connected_checkbox",
    },
    {
        "match_any": ["building permits"],
        "canonical_keys": ["building_permits_last_7_years"],
        "answer_type": "boolean",
    },
    {
        "match_any": ["required insurance", "policy insurance", "damage and destruction", "owner builder"],
        "canonical_keys": ["insured"],
        "answer_type": "boolean_to_text",
    },
    {
        "match_any": ["easement", "covenant", "restriction"],
        "canonical_keys": ["encumbrances_caveats_notices"],
        "answer_type": "text",
    },
    {
        "match_any": ["land tax reform scheme", "avpcc", "entry date for tax reform scheme"],
        "canonical_keys": ["land_tax_payable"],
        "answer_type": "review_only",
    },
    {
        "match_any": ["rates", "taxes", "charges", "outgoings"],
        "canonical_keys": ["council_rates", "land_tax_payable", "vacant_land_residential_land_tax_payable"],
        "answer_type": "grouped_text",
    },
]


def _find_rule(label: str | None) -> dict[str, Any] | None:
    if not label:
        return None

    lowered = label.lower()
    for rule in QUESTION_RULES:
        if any(token in lowered for token in rule["match_any"]):
            return rule
    return None


def _pick_best_fact(candidates: list[ArtifactFact]) -> ArtifactFact | None:
    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda item: (
            0 if item.status == "supported" else 1,
            -item.confidence,
        ),
    )[0]


def _resolve_target(target: QuestionTarget, fact_index: dict[str, list[ArtifactFact]]) -> QuestionTarget:
    rule = _find_rule(target.nearby_label)
    if not rule:
        target.notes.append("No mapping rule matched this field label.")
        return target

    target.mapped_canonical_keys = list(rule["canonical_keys"])
    candidate_facts: list[ArtifactFact] = []
    for key in rule["canonical_keys"]:
        candidate_facts.extend(fact_index.get(key, []))

    if not candidate_facts:
        target.notes.append("No candidate facts found for mapped canonical keys.")
        return target

    best = _pick_best_fact(candidate_facts)
    if best is None:
        return target

    target.supporting_fact_ids = [best.fact_id]
    target.evidence = best.evidence
    target.candidate_confidence = best.confidence

    if best.status == "conflicted":
        target.answer_status = "conflicted"
        target.notes.append("Best fact is conflicted.")
        return target

    if best.status == "incomplete":
        target.answer_status = "needs_review"
        target.candidate_answer = best.value
        target.notes.append("Best fact is incomplete.")
        return target

    answer_type = rule["answer_type"]
    value = best.value

    if answer_type == "boolean_true_means_checked":
        target.candidate_answer = bool(value)
        target.answer_status = "answered"
        return target

    if answer_type == "boolean_negative_checkbox":
        target.candidate_answer = not bool(value)
        target.answer_status = "answered"
        return target

    if answer_type == "service_not_connected_checkbox":
        target.candidate_answer = not bool(value)
        target.answer_status = "answered"
        return target

    if answer_type == "text":
        target.candidate_answer = value
        target.answer_status = "answered" if value not in (None, "", []) else "not_found"
        return target

    if answer_type == "list_or_boolean":
        target.candidate_answer = value
        target.answer_status = "answered"
        return target

    if answer_type in {"grouped_text", "boolean_attachment_style", "boolean_to_text"}:
        target.candidate_answer = value
        target.answer_status = "candidate_only"
        return target

    if answer_type == "review_only":
        target.candidate_answer = value
        target.answer_status = "needs_review"
        return target

    if answer_type == "boolean":
        target.candidate_answer = value
        target.answer_status = "answered"
        return target

    target.candidate_answer = value
    target.answer_status = "candidate_only"
    return target


def _extract_targets_from_yaml_docs(docs: list[Document]) -> list[QuestionTarget]:
    targets: list[QuestionTarget] = []
    yaml_docs = [doc for doc in docs if doc.document_type.value == "sec32_tab"]

    for doc in yaml_docs:
        tab_slug = (doc.yaml_tab_name or doc.filename).lower().replace(" ", "_").replace(".", "")
        for index, field in enumerate(doc.yaml_fields, start=1):
            targets.append(
                QuestionTarget(
                    target_id=f"{tab_slug}_{index}",
                    tab_name=doc.yaml_tab_name,
                    field_index=index,
                    control_type=field.control_type,
                    yaml_auto_id=field.auto_id,
                    yaml_name=field.name,
                    nearby_label=field.nearby_label,
                    dropdown_options=field.dropdown_options,
                )
            )

    return targets


def build_fill_artifact(extraction: FinalExtraction, docs: list[Document]) -> FillArtifact:
    facts = _flatten_facts(extraction)
    fact_index = _build_fact_index(facts)
    targets = [_resolve_target(target, fact_index) for target in _extract_targets_from_yaml_docs(docs)]
    review_queue = list(extraction.review_summary.get("quality_review_items", []))

    coverage_summary = {
        "documents_processed": len(docs),
        "fact_count": len(facts),
        "question_target_count": len(targets),
        "answered_count": sum(1 for item in targets if item.answer_status == "answered"),
        "candidate_only_count": sum(1 for item in targets if item.answer_status == "candidate_only"),
        "needs_review_count": sum(1 for item in targets if item.answer_status == "needs_review"),
        "not_found_count": sum(1 for item in targets if item.answer_status == "not_found"),
        "conflicted_count": sum(1 for item in targets if item.answer_status == "conflicted"),
    }

    return FillArtifact(
        documents=_documents_summary(docs),
        facts=facts,
        question_targets=targets,
        review_queue=review_queue,
        coverage_summary=coverage_summary,
    )
