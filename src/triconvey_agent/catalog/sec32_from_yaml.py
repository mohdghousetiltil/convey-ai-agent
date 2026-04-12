from __future__ import annotations

from dataclasses import dataclass

from triconvey_agent.schemas.documents import Document, DocumentType


@dataclass(frozen=True)
class DiscoveredSec32Question:
    label: str
    source_file: str
    tab_name: str | None
    control_type: str | None
    auto_id: str | None


def collect_sec32_questions_from_docs(docs: list[Document]) -> list[DiscoveredSec32Question]:
    seen: set[tuple[str, str, str, str]] = set()
    questions: list[DiscoveredSec32Question] = []

    for doc in docs:
        if doc.document_type != DocumentType.SEC32_TAB:
            continue

        for field in doc.yaml_fields:
            label = (field.nearby_label or field.name or "").strip()
            if not label:
                continue

            key = (
                label,
                doc.filename,
                field.control_type or "",
                field.auto_id or "",
            )
            if key in seen:
                continue
            seen.add(key)

            questions.append(
                DiscoveredSec32Question(
                    label=label,
                    source_file=doc.filename,
                    tab_name=doc.yaml_tab_name,
                    control_type=field.control_type,
                    auto_id=field.auto_id,
                )
            )

    return questions
