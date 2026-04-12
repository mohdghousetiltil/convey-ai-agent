from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from triconvey_agent.config import DocumentSignature, settings
from triconvey_agent.ingest.normalizer import (
    collapse_snippet,
    contains_all_terms,
    filename_key,
    normalize_scalar,
    normalize_text,
    to_search_blob,
)
from triconvey_agent.schemas.documents import (
    Document,
    DocumentType,
    FieldPosition,
    InputFileType,
    YamlField,
)


def _score_signature(
    signature: DocumentSignature,
    *,
    filename_blob: str,
    text_blob: str,
    tab_name_blob: str,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    for term in signature.filename_terms:
        if term in filename_blob:
            score += settings.filename_term_weight
            reasons.append(f"filename matched '{term}'")

    for term in signature.tab_name_terms:
        if term in tab_name_blob:
            score += settings.tab_name_term_weight
            reasons.append(f"tab name matched '{term}'")

    for term in signature.text_terms:
        if term in text_blob:
            score += settings.text_term_weight
            reasons.append(f"text matched '{term}'")

    for terms in signature.required_term_groups:
        if contains_all_terms(text_blob, terms) or contains_all_terms(tab_name_blob, terms):
            score += settings.required_group_weight
            reasons.append(f"text contained grouped terms {terms}")

    return score, reasons


def classify_yaml_document(
    path: Path,
    data: dict[str, Any],
    yaml_fields: list[YamlField],
) -> tuple[DocumentType, float, list[str]]:
    filename_blob = filename_key(path.name)
    tab_name_blob = to_search_blob(str(data.get("tab_name") or ""))
    field_text_blob = to_search_blob(
        *[
            " ".join(
                str(value)
                for value in (
                    field.control_type,
                    field.name,
                    field.nearby_label,
                    field.current_value,
                )
                if value not in (None, "")
            )
            for field in yaml_fields
        ]
    )

    best_type = DocumentType.UNKNOWN
    best_score = 0.0
    best_reasons: list[str] = []
    second_best = 0.0

    for signature in settings.document_signatures:
        score, reasons = _score_signature(
            signature,
            filename_blob=filename_blob,
            text_blob=field_text_blob,
            tab_name_blob=tab_name_blob,
        )
        if score > best_score:
            second_best = best_score
            best_type = signature.document_type
            best_score = score
            best_reasons = reasons
        elif score > second_best:
            second_best = score

    if best_type == DocumentType.UNKNOWN:
        return DocumentType.UNKNOWN, 0.0, []

    signature = next(item for item in settings.document_signatures if item.document_type == best_type)
    if best_score < signature.minimum_score:
        return DocumentType.UNKNOWN, 0.0, []

    confidence = min(0.99, 0.4 + (best_score / 10.0))
    if second_best > 0:
        confidence = max(0.0, confidence - min(0.25, (second_best / best_score) * settings.ambiguity_penalty))

    return best_type, round(confidence, 3), best_reasons


def _parse_yaml_fields(data: dict[str, Any]) -> list[YamlField]:
    fields: list[YamlField] = []

    for item in data.get("fields", []) or []:
        if not isinstance(item, dict):
            continue

        position_data = item.get("position")
        position = FieldPosition(**position_data) if isinstance(position_data, dict) else None

        field = YamlField(
            control_type=normalize_scalar(item.get("control_type")),
            auto_id=normalize_scalar(item.get("auto_id")),
            name=normalize_scalar(item.get("name")),
            class_name=normalize_scalar(item.get("class_name")),
            current_value=normalize_scalar(item.get("current_value")),
            nearby_label=normalize_scalar(item.get("nearby_label")),
            is_enabled=item.get("is_enabled"),
            is_checked=item.get("is_checked"),
            dropdown_options=[str(option) for option in item.get("dropdown_options") or []],
            position=position,
            raw=item,
        )
        fields.append(field)

    return fields


def load_yaml_document(path: str | Path) -> Document:
    file_path = Path(path).expanduser().resolve()

    with file_path.open("r", encoding=settings.default_encoding) as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML object at root for {file_path.name}")

    yaml_fields = _parse_yaml_fields(data)
    document_type, confidence, reasons = classify_yaml_document(file_path, data, yaml_fields)

    raw_text_parts: list[str] = []
    tab_name = data.get("tab_name")
    if tab_name:
        raw_text_parts.append(str(tab_name))

    for field in yaml_fields:
        parts = [
            field.control_type or "",
            field.name or "",
            field.nearby_label or "",
            str(field.current_value) if field.current_value is not None else "",
        ]
        joined = " | ".join(part for part in parts if part)
        if joined:
            raw_text_parts.append(joined)

    raw_text = "\n".join(raw_text_parts).strip()
    subtype = (
        normalize_text(str(data.get("tab_name") or ""))
        .lower()
        .replace(".", "")
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
    )
    metadata = {
        "loader": "yaml_loader",
        "file_size_bytes": file_path.stat().st_size,
        "source_subtype": subtype or None,
        "scan_date": data.get("scan_date"),
        "tab_name": normalize_scalar(data.get("tab_name")),
        "control_count": len(yaml_fields),
        "enabled_count": sum(1 for field in yaml_fields if field.is_enabled),
        "checkbox_count": sum(1 for field in yaml_fields if (field.control_type or "").lower() == "checkbox"),
        "text_preview": collapse_snippet(raw_text),
    }

    return Document(
        source_path=file_path,
        filename=file_path.name,
        file_type=InputFileType.YAML,
        document_type=document_type,
        classification_confidence=confidence,
        classification_reasons=reasons,
        raw_text=raw_text,
        normalized_text=normalize_text(raw_text),
        yaml_tab_name=normalize_scalar(data.get("tab_name")),
        yaml_total_fields=data.get("total_fields"),
        yaml_fields=yaml_fields,
        metadata=metadata,
    )
