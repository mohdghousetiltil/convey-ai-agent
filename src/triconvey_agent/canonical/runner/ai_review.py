from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from triconvey_agent.ai.client import AIClient

logger = logging.getLogger(__name__)


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
        "Preserve exact dates from evidence and normalize standalone dates to DD/MM/YYYY.\n"
        "If the field is an amount and the evidence shows '-', 'n/a', or a blank placeholder, suggest 0.00.\n"
        "Never guess missing dates or amounts beyond those rules.\n"
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


def _review_one_answer(qid: str, answer, question, ai_client: AIClient) -> tuple[str, dict]:
    response = ai_client.complete(build_ai_review_prompt(question, answer))
    parsed = extract_json_dict(response.raw_text)
    if parsed is None:
        return qid, {
            "status": "invalid_json",
            "suggested_value": None,
            "confidence": 0.0,
            "quote": None,
            "source_file": None,
            "reason": "AI review did not return valid JSON.",
            "raw_text": response.raw_text,
        }

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

    suggestion = {
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
        suggestion["status"] = "invalid_quote"
        suggestion["reason"] = "AI review quote did not match the provided evidence."
    return qid, suggestion


_SKIP_AI_REVIEW_QUESTION_IDS = frozenset({
    # Council rates — extracted deterministically by rule; AI adds no value.
    "sec32_1.1_outgoing_1_authority",
    "sec32_1.1_outgoing_1_amount",
})

_SKIP_AI_REVIEW_SOURCES = frozenset({
    "rates_and_charges_block",
    "current_rates_date_levied_block",
    "current_rates_levied_line",
    "explicit_subtotal[0]",
    "explicit_subtotal[1]",
    "explicit_subtotal[2]",
    "explicit_subtotal[3]",
    "total_charges_line",
})


def should_skip_ai_review(answer) -> bool:
    """Return True when AI review would add no value for this answer."""
    # High-confidence rule-based answers don't need AI verification.
    if getattr(answer, "confidence", 0.0) >= 0.97:
        return True

    # No value and no evidence — AI has nothing to work with.
    # This covers: "no facts found", "no candidate evidence", policy-default
    # questions, vendor-form fields, and human_review placeholders that were
    # never populated from uploaded documents.
    evidence = getattr(answer, "evidence", []) or []
    if answer.value is None and not evidence:
        return True

    # Skip hardcoded / policy / injected answers (evidence present but not real docs)
    for source in evidence:
        file_name = (getattr(source, "file", "") or "").lower()
        quote = (getattr(source, "quote", "") or "").lower()

        if any(token in file_name for token in ["policy", "hardcoded", "system", "injected"]):
            return True

        if any(token in quote for token in [
            "policy facts injected",
            "hardcoded",
            "default answer",
            "injected policy",
            "copy rule",
        ]):
            return True

    return False


def run_ai_review(answers: dict, registry: dict, ai_client: AIClient) -> dict[str, dict]:
    started = time.perf_counter()
    review_targets: list[tuple[str, object, object]] = []
    for qid, answer in answers.items():
        question = registry.get(qid)
        if question is None:
            continue
        # STRICT: only review questions explicitly flagged for human/AI review.
        # Do NOT send confirmed answers to AI — that is "deep review" mode and
        # is too slow.  The AI pass is reserved for genuine ambiguities where
        # the rule-based extractor could not pick a winner.
        if not answer.needs_review:
            continue
        if qid in _SKIP_AI_REVIEW_QUESTION_IDS:
            continue
        if should_skip_ai_review(answer):
            logger.info(
                "[AI Review] skipped %s: confidence=%s reasons=%s",
                qid,
                getattr(answer, "confidence", None),
                getattr(answer, "review_reasons", []),
            )
            continue
        review_targets.append((qid, answer, question))

    suggestions: dict[str, dict] = {}
    if not review_targets:
        return suggestions

    logger.info(
        "[AI Review] reviewing %d/%d field(s): %s",
        len(review_targets),
        len(answers),
        [qid for qid, _, _ in review_targets],
    )
    print(
        "  [AI Review] Queue:",
        [
            {
                "qid": qid,
                "value": answer.value,
                "confidence": answer.confidence,
                "needs_review": answer.needs_review,
                "reasons": getattr(answer, "review_reasons", []),
                "evidence": [
                    {
                        "file": getattr(src, "file", None),
                        "quote": getattr(src, "quote", None),
                    }
                    for src in getattr(answer, "evidence", []) or []
                ],
            }
            for qid, answer, _ in review_targets
        ],
    )
    print(f"  [AI Review] Reviewing {len(review_targets)} field(s)...")
    max_workers = min(
        len(review_targets),
        max(1, int(os.getenv("CONVEY_AI_REVIEW_MAX_WORKERS", "4"))),
    )

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_review_one_answer, qid, answer, question, ai_client): qid
            for qid, answer, question in review_targets
        }
        for future in as_completed(future_map):
            qid = future_map[future]
            try:
                result_qid, result = future.result()
                suggestions[result_qid] = result
            except Exception as exc:
                suggestions[qid] = {
                    "status": "error",
                    "suggested_value": None,
                    "confidence": 0.0,
                    "quote": None,
                    "source_file": None,
                    "reason": f"AI review failed: {exc}",
                    "raw_text": "",
                }
            completed += 1
            if completed == len(review_targets) or completed % 5 == 0:
                print(f"  [AI Review] {completed}/{len(review_targets)} complete")

    print(f"  [Time] AI Review total: {time.perf_counter() - started:.2f}s")
    return suggestions
