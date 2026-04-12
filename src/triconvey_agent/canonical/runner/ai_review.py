from __future__ import annotations

import json

from triconvey_agent.ai.client import AIClient


def extract_json_dict(raw_text: str) -> dict | None:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return None
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(raw_text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def build_ai_review_prompt(question, answer) -> str:
    evidence_lines: list[str] = []
    for idx, source in enumerate(answer.evidence, start=1):
        evidence_lines.append(
            "\n".join(
                [
                    f"[{idx}] file: {source.file}",
                    f"quote: {source.quote or ''}",
                ]
            )
        )
    return (
        "You are strictly reviewing a conveyancing answer.\n"
        "Use ONLY the evidence below. Do not infer missing facts.\n"
        "If the current answer is supported, confirm it.\n"
        "If a different value is better supported, suggest it.\n"
        "If the evidence is insufficient, say so.\n"
        "Return ONLY strict JSON with keys: "
        "status, suggested_value, confidence, quote, source_file, reason.\n"
        "Allowed status values: confirmed, suggest_change, insufficient_evidence.\n\n"
        f"Question ID: {question.id}\n"
        f"Question Label: {question.label}\n"
        f"Current Value: {answer.value!r}\n"
        f"Current Needs Review: {answer.needs_review}\n"
        f"Expected Type: {question.expected_type or 'unknown'}\n"
        f"Allowed Options: {', '.join(question.options or []) or 'none'}\n"
        f"Description: {question.description or ''}\n\n"
        "Evidence:\n"
        f"{chr(10).join(evidence_lines)}\n"
    )


def run_ai_review(answers: dict, registry: dict, ai_client: AIClient) -> dict[str, dict]:
    suggestions: dict[str, dict] = {}
    for qid, answer in answers.items():
        question = registry.get(qid)
        if question is None or not answer.evidence:
            continue
        if answer.value is None and not answer.needs_review:
            continue

        response = ai_client.complete(build_ai_review_prompt(question, answer))
        parsed = extract_json_dict(response.raw_text)
        if parsed is None:
            suggestions[qid] = {
                "status": "invalid_json",
                "suggested_value": None,
                "confidence": 0.0,
                "quote": None,
                "source_file": None,
                "reason": "AI review did not return valid JSON.",
                "raw_text": response.raw_text,
            }
            continue

        quote = parsed.get("quote")
        source_file = str(parsed.get("source_file") or "")
        verified = False
        if quote:
            for source in answer.evidence:
                source_quote = source.quote or ""
                if (quote == source_quote or quote in source_quote) and (
                    not source_file or source.file == source_file
                ):
                    verified = True
                    break
        elif parsed.get("status") == "insufficient_evidence":
            verified = True

        suggestions[qid] = {
            "status": parsed.get("status", "invalid_json"),
            "suggested_value": parsed.get("suggested_value"),
            "confidence": parsed.get("confidence", 0.0),
            "quote": quote,
            "source_file": source_file or None,
            "reason": parsed.get("reason"),
            "quote_verified": verified,
            "raw_text": response.raw_text,
        }
        if not verified:
            suggestions[qid]["status"] = "invalid_quote"
            suggestions[qid]["reason"] = "AI review quote did not match the provided evidence."

    return suggestions
