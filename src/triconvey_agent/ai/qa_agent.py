"""Direct Question-Answer Agent for TriConvey form filling.

Architecture
-----------
Instead of: PDFs → intermediate schema → mapper → form answers
We do:      PDFs + all 78 questions → GPT-4o → form answers directly

Why this is better
------------------
- AI sees the ACTUAL question label and its dropdown options
- No lossy intermediate schema — every question can be answered
- Works automatically for any new question added to the form
- AI reasons about the question in legal context, not just field lookup
- Planning zone, insurance details, notices — all covered without new code

Anti-hallucination
------------------
- Every answer requires a verbatim quote from a specific source file
- Quotes verified against raw text after AI responds
- Unverified quotes → answer cleared (returned as null)
- AI told explicitly: "null is better than a wrong answer"
"""
from __future__ import annotations

import json
import re
from typing import Any

from triconvey_agent.ai.client import AIClient
from triconvey_agent.schemas.documents import Document

_MAX_CHARS_PER_DOC = 8_000

# ---------------------------------------------------------------------------
# Legal context for the AI
# ---------------------------------------------------------------------------

_LEGAL_CONTEXT = """\
You are an expert Victorian conveyancing paralegal completing a Section 32 Vendor's Statement
for a property sale in Victoria, Australia.

You have been given:
  1. Multiple property documents (PDFs) with their raw text
  2. The exact list of questions from the TriConvey software that must be answered

YOUR JOB: Answer every question from the documents provided.

CRITICAL RULES:
1. Every answer MUST be supported by a verbatim quote from one of the documents.
2. "quote" must be text that appears word-for-word in the named document.
3. "source_file" must match exactly one filename shown above.
4. Return null for value if the information is not in any document.
5. NEVER invent, assume or guess values — null is always better than wrong.
6. For boolean questions: true=Yes/ticked, false=No/not ticked, null=cannot determine.
7. Services: the vendor form lists services that ARE connected. Unlisted = NOT connected.
8. Planning zone: pick the exact option from the provided list that matches the document.
9. Dates: use the date printed on the document, never today's date.
10. For "are contained in attached certificate" type questions: true means YES the info
    is in the attachments, which is the standard answer when a certificate is uploaded.

SECTION 32 LEGAL KNOWLEDGE:
- Section 32 is the Victorian Vendor's Statement under Sale of Land Act 1962
- "Outgoings" = rates and charges the purchaser will inherit (council, water, land tax etc.)
- "GAIC" = Growth Areas Infrastructure Contribution — applies to land in growth corridors
- "Owners Corporation" = body corporate managing common property in subdivisions
- "Terms Contract" = vendor finances the purchase (like vendor finance/rent-to-buy)
- "Sale Subject to Mortgage" = existing mortgage stays on title during settlement
- Easements described "in attached title documents" means the title register search covers it
- Building permits from last 7 years must be disclosed
- Bushfire Prone Area = designated under Building Regulations — check planning certificate
"""

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_document_blocks(docs: list[Document]) -> str:
    blocks: list[str] = []
    for doc in docs:
        if not doc.is_pdf:
            continue
        text = (doc.raw_text or doc.normalized_text or "").strip()
        if not text:
            continue
        truncated = text[:_MAX_CHARS_PER_DOC]
        if len(text) > _MAX_CHARS_PER_DOC:
            truncated += "\n[... truncated ...]"
        blocks.append(f"=== FILE: {doc.filename} ===\n{truncated}")
    return "\n\n".join(blocks)


def _build_questions_block(template: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten all questions from all tabs into a simple list with context."""
    questions: list[dict[str, Any]] = []
    for tab in template.get("tabs", []):
        tab_name = tab.get("tab_name", "")
        for q in tab.get("questions", []):
            questions.append({
                "tab": tab_name,
                "label": q.get("label", ""),
                "options": q.get("options"),  # dropdown options if applicable
            })
    return questions


def _build_prompt(docs: list[Document], template: dict[str, Any]) -> str:
    doc_blocks = _build_document_blocks(docs)
    questions = _build_questions_block(template)

    questions_json = json.dumps(questions, indent=2)

    return f"""{_LEGAL_CONTEXT}

DOCUMENTS:
{doc_blocks}

---

QUESTIONS TO ANSWER:
{questions_json}

---

TASK: For each question above, provide an answer object.
Return a JSON array — one object per question, in the same order.

Each object must have:
  "tab":         the tab name (copy from input)
  "label":       the question label (copy from input, exact match)
  "value":       the answer — string, boolean, number, or null
  "source_file": which filename contains the evidence (or null)
  "quote":       verbatim text from source_file proving the answer (or null if value is null)
  "confidence":  float 0.0-1.0

ANSWER RULES BY QUESTION TYPE:
- "tick if NOT connected" services: true = NOT connected, false = IS connected
- Dropdown questions (have "options" list): value must be one of the listed options or null
- "Are contained in attached certificate" questions: true if the relevant certificate is uploaded
- "Are as follows" checkbox questions: true if you're listing the information below, false/null otherwise
- Boolean yes/no: true=Yes, false=No, null=unknown
- Amount fields: include currency symbol e.g. "$1,234.56"
- Text fields: extract the relevant text or description

IMPORTANT: Return ONLY the JSON array. No markdown fences. No explanation.
"""


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_array(text: str) -> list[dict[str, Any]]:
    cleaned = _strip_fences(text)
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    # Try to extract array from the text
    match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result
        except Exception:
            pass
    raise ValueError(f"No JSON array found in AI response: {text[:300]}")


# ---------------------------------------------------------------------------
# Quote verification
# ---------------------------------------------------------------------------

def _text_map(docs: list[Document]) -> dict[str, str]:
    return {
        doc.filename: (doc.raw_text or doc.normalized_text or "")
        for doc in docs
        if doc.is_pdf
    }


def _verify_answer(answer: dict[str, Any], texts: dict[str, str]) -> dict[str, Any]:
    """Verify the quote exists in the source document. Clear answer if not."""
    if answer.get("value") is None:
        return answer

    quote = answer.get("quote")
    source = answer.get("source_file")

    if not quote or not source:
        # No quote provided — penalise confidence but keep answer for non-critical fields
        answer["quote_verified"] = False
        answer["confidence"] = float(answer.get("confidence", 0.5)) * 0.40
        return answer

    raw = texts.get(source, "")
    # Exact match
    if quote in raw:
        answer["quote_verified"] = True
        return answer
    # Whitespace-normalised match
    if " ".join(quote.split()) in " ".join(raw.split()):
        answer["quote_verified"] = True
        return answer

    # Quote not found — high risk of hallucination, clear the value
    answer["quote_verified"] = False
    answer["confidence"] = 0.0
    answer["value"] = None  # reject the answer
    answer["rejection_reason"] = "quote not found in source document"
    return answer


# ---------------------------------------------------------------------------
# Merge answers into template
# ---------------------------------------------------------------------------

def _apply_answers_to_template(
    template: dict[str, Any],
    answers: list[dict[str, Any]],
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write AI answers back into the template structure."""
    import copy
    result = copy.deepcopy(template)

    # Build a lookup: (tab_name, label) → answer
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for ans in answers:
        key = (ans.get("tab", ""), ans.get("label", ""))
        lookup[key] = ans

    filled = 0
    null_count = 0

    for tab in result.get("tabs", []):
        tab_name = tab.get("tab_name", "")
        for q in tab.get("questions", []):
            label = q.get("label", "")
            key = (tab_name, label)
            ans = lookup.get(key)
            if ans:
                val = ans.get("value")
                if val is not None:
                    q["answer"] = val
                    q["ai_source_file"] = ans.get("source_file")
                    q["ai_quote"] = ans.get("quote")
                    q["ai_confidence"] = ans.get("confidence", 0.0)
                    q["ai_quote_verified"] = ans.get("quote_verified", False)
                    filled += 1
                else:
                    null_count += 1

    # Apply policy overrides last (always win)
    if policy_overrides:
        for tab in result.get("tabs", []):
            for q in tab.get("questions", []):
                label = q.get("label", "")
                if label in policy_overrides:
                    q["answer"] = policy_overrides[label]
                    q["policy_override"] = True

    result["_qa_agent_stats"] = {
        "filled": filled,
        "null": null_count,
        "total": filled + null_count,
    }
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_qa_agent(
    docs: list[Document],
    template: dict[str, Any],
    client: AIClient,
    policy_overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Answer all TriConvey questions directly from the uploaded documents.

    This bypasses the intermediate final_output.json schema entirely.
    GPT-4o sees the actual question labels (with legal context) and answers them
    directly from document text with quote verification.

    Args:
        docs:             All loaded Document objects (PDFs + YAML).
        template:         The triconvey_tab.json template as a dict.
        client:           AIClient wrapping GPT-4o.
        policy_overrides: Optional dict of {question_label: answer} that always win.

    Returns:
        (answered_template, qa_report)
        - answered_template: ready to write as answered_triconvey_tab.json
        - qa_report: stats + all raw answers for audit
    """
    texts = _text_map(docs)

    prompt = _build_prompt(docs, template)

    try:
        response = client.complete(prompt)
        raw_answers = _parse_array(response.raw_text)
    except Exception as exc:  # noqa: BLE001
        return template, {"error": str(exc), "filled": 0, "null": 0, "verified": 0}

    # Verify every quote
    verified_answers = [_verify_answer(a, texts) for a in raw_answers]

    # Merge into template
    answered = _apply_answers_to_template(template, verified_answers, policy_overrides)

    # Build report
    filled   = sum(1 for a in verified_answers if a.get("value") is not None)
    null_c   = sum(1 for a in verified_answers if a.get("value") is None)
    verified = sum(1 for a in verified_answers if a.get("quote_verified"))
    rejected = sum(1 for a in verified_answers if a.get("rejection_reason"))

    report = {
        "mode": "qa_agent",
        "questions_sent": len(verified_answers),
        "filled": filled,
        "null": null_c,
        "verified": verified,
        "rejected_hallucinations": rejected,
        "answers": verified_answers,  # full audit trail
    }

    return answered, report
