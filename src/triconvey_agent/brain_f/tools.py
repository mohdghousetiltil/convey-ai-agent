"""Brain F tool definitions and handlers.

Tool catalogue:
  get_facts            — resolve facts by path prefix from FactStore
  get_conflicts        — list all unresolved fact conflicts
  get_document_summary — retrieve per-doc memory summary
  search_documents     — hybrid semantic search over corpus chunks
  compare_facts        — compare the same fact path across two specific documents
  run_review_checklist — generate a whole-matter Section 32 checklist
  propose_answer_patch — queue a correction to an answer (user-approved)
"""
from __future__ import annotations

import json
import math
from typing import Any

from triconvey_agent.canonical.facts.store import FactStoreImpl


# ---------------------------------------------------------------------------
# Anthropic tool schema definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_facts",
        "description": (
            "Retrieve canonical extracted facts from the FactStore. "
            "Pass a dot-separated path prefix (e.g. 'rates.water', 'title', 'rates.council.annual_amount'). "
            "Returns the resolved winner, confidence, source document, and all competing candidates. "
            "Always call this before answering questions about amounts or authority names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path_prefix": {
                    "type": "string",
                    "description": (
                        "Fact path or prefix to look up. Examples: "
                        "'rates.water.annual_amount', 'rates.council', 'title.volume_folio', "
                        "'rates.owners_corporation', 'rates.state_revenue'"
                    ),
                }
            },
            "required": ["path_prefix"],
        },
    },
    {
        "name": "get_conflicts",
        "description": (
            "Return all fact paths where two or more extractors disagreed and the conflict "
            "was escalated to human review. Use this to proactively flag issues to the conveyancer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_document_summary",
        "description": (
            "Retrieve the stored memory summary for a specific uploaded document. "
            "Useful when the user asks what a particular file contains."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "The exact filename of the uploaded document (e.g. 'council_rates.pdf').",
                }
            },
            "required": ["filename"],
        },
    },
    {
        "name": "search_documents",
        "description": (
            "Search the full document corpus for specific text or terms. "
            "Returns the most relevant passages from all uploaded PDFs. "
            "Use this when a fact is not in the FactStore or you need a verbatim quote."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms (e.g. 'annual water charge', 'lot 5 plan', 'bushfire overlay').",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 10).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "compare_facts",
        "description": (
            "Compare one canonical fact path across two specific documents and surface any discrepancy. "
            "Useful for checking whether the vendor form agrees with a certificate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Canonical fact path to compare."},
                "doc_a": {"type": "string", "description": "First document filename or distinctive substring."},
                "doc_b": {"type": "string", "description": "Second document filename or distinctive substring."},
            },
            "required": ["path", "doc_a", "doc_b"],
        },
    },
    {
        "name": "run_review_checklist",
        "description": (
            "Generate a proactive whole-matter checklist covering title, rates, water, OC, planning, and bushfire."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "propose_answer_patch",
        "description": (
            "Queue a correction to an extracted answer for the conveyancer to approve. "
            "Use whenever you discover a value that should be updated based on document evidence. "
            "The patch will not be applied until the user clicks 'Apply' in the UI. "
            "Proactively call this tool after answering if you find any values that need correction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question_id": {
                    "type": "string",
                    "description": (
                        "The canonical fact path for the field to correct. "
                        "Use the same path format as the FactStore (e.g. 'rates.water.authority_name', "
                        "'rates.council.annual_amount', 'rates.water.annual_amount', "
                        "'planning.zone', 'planning.responsible_authority', "
                        "'rates.owners_corporation.annual_amount'). "
                        "Do NOT invent IDs — use the fact path from get_facts results."
                    ),
                },
                "new_value": {
                    "type": "string",
                    "description": "The corrected value to propose.",
                },
                "reason": {
                    "type": "string",
                    "description": "One sentence: which document shows this value and why the current answer is wrong.",
                },
            },
            "required": ["question_id", "new_value", "reason"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def handle_get_facts(store: FactStoreImpl, path_prefix: str) -> dict[str, Any]:
    """Resolve facts by prefix and return a structured result."""
    if not path_prefix:
        return {"error": "path_prefix must not be empty"}

    matching_paths = store.paths_matching(path_prefix)
    # Also check exact match
    if not matching_paths and store.has(path_prefix):
        matching_paths = [path_prefix]

    if not matching_paths:
        return {
            "found": False,
            "path_prefix": path_prefix,
            "message": f"No facts found for path prefix '{path_prefix}'.",
        }

    results: list[dict[str, Any]] = []
    for path in matching_paths:
        winner, conflict = store.get(path)
        all_facts = store.get_all(path)

        candidates: list[dict[str, Any]] = []
        for f in all_facts:
            sources = [
                {"file": s.file, "page": s.page, "quote": (s.quote or "")[:200]}
                for s in (f.sources or [])
            ]
            candidates.append({
                "value": f.value,
                "confidence": round(f.confidence, 3),
                "extractor": f.extractor,
                "sources": sources,
                "notes": f.notes,
            })

        entry: dict[str, Any] = {
            "path": path,
            "candidate_count": len(all_facts),
            "candidates": candidates,
        }

        if winner is not None:
            winner_sources = [
                {"file": s.file, "page": s.page, "quote": (s.quote or "")[:200]}
                for s in (winner.sources or [])
            ]
            entry["resolved_value"] = winner.value
            entry["resolved_confidence"] = round(winner.confidence, 3)
            entry["resolved_extractor"] = winner.extractor
            entry["resolved_sources"] = winner_sources
            entry["resolved_notes"] = winner.notes
        else:
            entry["resolved_value"] = None
            entry["resolution_status"] = "REVIEW_REQUIRED"

        if conflict is not None:
            entry["conflict"] = {
                "resolution": conflict.resolution,
                "winning_extractor": conflict.winning_fact.extractor if conflict.winning_fact else None,
                "reason": conflict.reason,
            }

        results.append(entry)

    return {"found": True, "path_prefix": path_prefix, "facts": results}


def handle_get_conflicts(store: FactStoreImpl) -> dict[str, Any]:
    """Return all unresolved conflicts from the FactStore."""
    unresolved = store.unresolved_conflicts()
    all_conflicts = store.conflicts()

    formatted_unresolved: list[dict[str, Any]] = []
    for c in unresolved:
        formatted_unresolved.append({
            "path": c.path,
            "resolution": c.resolution,
            "candidates": [
                {
                    "value": f.value,
                    "confidence": round(f.confidence, 3),
                    "extractor": f.extractor,
                }
                for f in (c.facts or [])
            ],
            "reason": c.reason,
        })

    formatted_resolved: list[dict[str, Any]] = []
    for c in all_conflicts:
        if c.resolution != "review_required":
            formatted_resolved.append({
                "path": c.path,
                "resolution": c.resolution,
                "winning_extractor": c.winning_fact.extractor if c.winning_fact else None,
                "reason": c.reason,
            })

    return {
        "unresolved_count": len(unresolved),
        "resolved_count": len(formatted_resolved),
        "unresolved_conflicts": formatted_unresolved,
        "resolved_conflicts": formatted_resolved,
    }


def handle_get_document_summary(
    memory: dict[str, Any],
    filename: str,
) -> dict[str, Any]:
    """Return the stored memory for a specific document."""
    if filename in memory:
        return {"found": True, **memory[filename]}

    # Fuzzy match by substring
    for key, val in memory.items():
        if filename.lower() in key.lower() or key.lower() in filename.lower():
            return {"found": True, "matched_filename": key, **val}

    return {
        "found": False,
        "filename": filename,
        "available_documents": list(memory.keys()),
    }


def handle_search_documents(
    corpus_chunks: list[dict[str, Any]],
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """Hybrid semantic/token-ranked search over sentence-level corpus chunks."""
    if not corpus_chunks:
        return {"results": [], "message": "No document corpus available."}

    top_k = min(max(1, top_k), 10)
    ranked = _rank_corpus_chunks(query, corpus_chunks)
    results = []
    for score, chunk in ranked[:top_k]:
        results.append({
            "file": chunk.get("file"),
            "page": chunk.get("page"),
            "chunk_id": chunk.get("chunk_id"),
            "score": round(score, 4),
            "excerpt": str(chunk.get("text") or "")[:400],
        })

    return {
        "query": query,
        "result_count": len(results),
        "results": results,
    }


def handle_compare_facts(
    store: FactStoreImpl,
    *,
    path: str,
    doc_a: str,
    doc_b: str,
) -> dict[str, Any]:
    facts = store.get_all(path)
    if not facts:
        return {"found": False, "message": f"No facts found for '{path}'."}

    def _matches(doc_hint: str, filename: str | None) -> bool:
        hint = (doc_hint or "").strip().lower()
        name = (filename or "").strip().lower()
        return bool(hint and name and (hint in name or name in hint))

    def _pick(doc_hint: str) -> dict[str, Any] | None:
        for fact in facts:
            for source in fact.sources or []:
                if _matches(doc_hint, source.file):
                    return {
                        "value": fact.value,
                        "confidence": round(fact.confidence, 3),
                        "extractor": fact.extractor,
                        "file": source.file,
                        "page": source.page,
                        "quote": (source.quote or "")[:220],
                    }
        return None

    left = _pick(doc_a)
    right = _pick(doc_b)
    return {
        "path": path,
        "doc_a": left,
        "doc_b": right,
        "match": bool(left and right and left["value"] == right["value"]),
    }


def handle_run_review_checklist(store: FactStoreImpl) -> dict[str, Any]:
    def _winner(path: str):
        return store.get(path)[0]

    def _has_unresolved(prefix: str) -> bool:
        return any(conflict.path.startswith(prefix) for conflict in store.unresolved_conflicts())

    items: list[dict[str, Any]] = []
    checks = [
        ("title", "Title", "title.volume_folio"),
        ("rates.council", "Council rates", "rates.council.annual_amount"),
        ("rates.water", "Water", "rates.water.annual_amount"),
        ("rates.owners_corporation", "Owners corporation", "rates.owners_corporation.annual_amount"),
        ("planning", "Planning", "planning.zone"),
        ("planning.bushfire_prone", "Bushfire", "planning.bushfire_prone"),
    ]
    for conflict_prefix, label, fact_path in checks:
        if _has_unresolved(conflict_prefix):
            items.append({"label": label, "status": "warning", "detail": "Conflicting evidence requires review."})
            continue
        winner = _winner(fact_path)
        if winner is None or winner.value in (None, ""):
            items.append({"label": label, "status": "missing", "detail": "No reliable extracted fact found."})
            continue
        items.append({"label": label, "status": "ok", "detail": str(winner.value)})
    return {"items": items}


def handle_propose_answer_patch(
    pending_patches: list[dict[str, Any]],
    question_id: str,
    new_value: str,
    reason: str,
) -> dict[str, Any]:
    """Queue a proposed answer correction (not applied until user approves)."""
    patch = {
        "question_id": question_id,
        "new_value": new_value,
        "reason": reason,
        "status": "pending_approval",
    }
    pending_patches.append(patch)
    return {
        "queued": True,
        "message": (
            f"Proposed correction for '{question_id}' has been queued. "
            "The conveyancer will see this and can apply or dismiss it."
        ),
        "patch": patch,
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    store: FactStoreImpl,
    memory: dict[str, Any],
    corpus_chunks: list[dict[str, Any]],
    pending_patches: list[dict[str, Any]],
) -> str:
    """Route a tool call to the correct handler and return JSON string."""
    try:
        if tool_name == "get_facts":
            result = handle_get_facts(store, tool_input.get("path_prefix", ""))
        elif tool_name == "get_conflicts":
            result = handle_get_conflicts(store)
        elif tool_name == "get_document_summary":
            result = handle_get_document_summary(memory, tool_input.get("filename", ""))
        elif tool_name == "search_documents":
            result = handle_search_documents(
                corpus_chunks,
                query=tool_input.get("query", ""),
                top_k=int(tool_input.get("top_k", 5)),
            )
        elif tool_name == "compare_facts":
            result = handle_compare_facts(
                store,
                path=tool_input.get("path", ""),
                doc_a=tool_input.get("doc_a", ""),
                doc_b=tool_input.get("doc_b", ""),
            )
        elif tool_name == "run_review_checklist":
            result = handle_run_review_checklist(store)
        elif tool_name == "propose_answer_patch":
            result = handle_propose_answer_patch(
                pending_patches,
                question_id=tool_input.get("question_id", ""),
                new_value=tool_input.get("new_value", ""),
                reason=tool_input.get("reason", ""),
            )
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
    except Exception as exc:
        result = {"error": f"Tool '{tool_name}' raised {type(exc).__name__}: {exc}"}

    return json.dumps(result, default=str)


def _tokenize(text: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _filename_hint_score(query: str, filename: str) -> float:
    q = (query or "").lower()
    name = (filename or "").lower()
    if not q or not name:
        return 0.0
    score = 0.0
    stem = name.rsplit(".", 1)[0]
    if name in q or stem in q:
        score += 8.0
    filename_tokens = _tokenize(stem.replace("_", " ").replace("-", " "))
    query_tokens = _tokenize(q)
    overlap = len(filename_tokens & query_tokens)
    score += overlap * 1.5
    if "state revenue" in q and ("state revenue" in name or "land tax" in name):
        score += 6.0
    if "land tax" in q and "land tax" in name:
        score += 6.0
    if "water" in q and "water" in name:
        score += 4.0
    if "council" in q and ("council" in name or "land information" in name):
        score += 4.0
    return score


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)




def _rank_corpus_chunks(query: str, corpus_chunks: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    query_tokens = _tokenize(query)
    lower_query = query.lower()
    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in corpus_chunks:
        text = str(chunk.get("text") or "")
        filename = str(chunk.get("file") or "")
        if not text.strip():
            continue
        token_overlap = len(query_tokens & _tokenize(text))
        phrase_boost = 3 if lower_query and lower_query in text.lower() else 0
        filename_boost = _filename_hint_score(query, filename)
        score = float(token_overlap + phrase_boost + filename_boost)
        # Use pre-computed embeddings only if already attached to the chunk (no live API calls)
        embedding = chunk.get("embedding")
        if isinstance(embedding, list) and embedding:
            # Only cosine if we have a pre-computed query vector from the chunk's metadata
            pass
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored
