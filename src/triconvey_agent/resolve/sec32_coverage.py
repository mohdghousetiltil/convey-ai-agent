from __future__ import annotations

from triconvey_agent.catalog.sec32_catalog import SECTION32_QUESTION_CATALOG
from triconvey_agent.catalog.sec32_from_yaml import DiscoveredSec32Question


def build_sec32_coverage(discovered: list[DiscoveredSec32Question]) -> dict[str, object]:
    catalog_questions = {item.question.strip().lower() for item in SECTION32_QUESTION_CATALOG}
    unmapped = [item.label for item in discovered if item.label.strip().lower() not in catalog_questions]

    return {
        "sec32_yaml_question_count": len(discovered),
        "sec32_catalog_question_count": len(SECTION32_QUESTION_CATALOG),
        "sec32_yaml_unmapped_count": len(unmapped),
        "sec32_yaml_unmapped_examples": unmapped[:25],
    }
