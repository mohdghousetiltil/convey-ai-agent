from __future__ import annotations

import json
from typing import Any

from triconvey_agent.ai.client import AIClient
from triconvey_agent.ai.prompts import build_review_prompt
from triconvey_agent.quality.review_rules import QualityReviewReport
from triconvey_agent.schemas.extracted import FinalExtraction


def run_ai_review(
    extraction: FinalExtraction,
    quality_report: QualityReviewReport,
    client: AIClient,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []

    for item in quality_report.items:
        section = getattr(extraction, item.section, {})
        field = section.get(item.field_key)
        if field is None or not field.evidence:
            continue

        snippet = field.evidence[0].snippet or ""
        prompt = build_review_prompt(item.field_key, field.value, snippet)
        response = client.complete(prompt)

        try:
            parsed = json.loads(response.raw_text)
        except Exception:
            parsed = {
                "field_key": item.field_key,
                "suggested_value": None,
                "confidence": 0.0,
                "reason": "AI response was not valid JSON.",
                "requires_review": True,
            }

        suggestions.append(parsed)

    return suggestions
