"""
Agent-tier tools (Feature 3: agentic reasoning).

Four additional tools the agent can call:
  run_pipeline        — trigger the document extraction pipeline
  query_facts         — free-text fact lookup against the FactStore
  check_sec32_coverage — report which Sec 32 fields are filled / missing
  search_memory       — search vector memory for relevant past context

These definitions follow the same Anthropic-native schema format used by
the existing TOOL_DEFINITIONS in brain_f/tools.py so they can be merged
directly into the tool list passed to any provider.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema definitions (Anthropic-native format)
# ---------------------------------------------------------------------------

AGENT_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "run_pipeline",
        "description": (
            "Trigger the document extraction pipeline on a list of document paths. "
            "Use this when the user asks to re-process, re-extract, or refresh data "
            "from specific uploaded documents. Returns a status report."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of document filenames or absolute paths to re-process. "
                        "Example: ['council_rates.pdf', 'section32.pdf']"
                    ),
                }
            },
            "required": ["document_paths"],
        },
    },
    {
        "name": "query_facts",
        "description": (
            "Search the FactStore for facts relevant to a natural-language question. "
            "Unlike get_facts (which requires an exact path), query_facts accepts plain "
            "English and returns matching canonical facts from all path prefixes. "
            "Use this when you are not sure which fact path to look up."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Natural-language question or keyword(s) to search. "
                        "Examples: 'what is the annual water charge?', "
                        "'council rates authority name', 'planning zone overlay'."
                    ),
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "check_sec32_coverage",
        "description": (
            "Return a structured report of which Section 32 (Vendor's Statement) "
            "fields are filled, missing, or flagged for review. "
            "Use this to give the conveyancer an at-a-glance coverage summary "
            "before answering completeness questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "search_memory",
        "description": (
            "Search vector memory for relevant context from past conversations "
            "about this or similar matters. Returns the top matching exchanges. "
            "Use when the user asks a question that may have been discussed before, "
            "or when additional context from previous sessions would help."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — a phrase or question to match against past exchanges.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 5, max 10).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def handle_run_pipeline(
    document_paths: list[str],
    run_dir: Any | None = None,
) -> dict[str, Any]:
    """
    Trigger (or simulate) the extraction pipeline on the given document paths.

    In a live session the agent has read-only access to already-processed data,
    so this tool reports on what would be re-extracted rather than actually
    re-running the full pipeline (which is a heavy background job triggered via
    the upload endpoint).  It returns actionable status so the agent can advise
    the user.
    """
    if not document_paths:
        return {"error": "document_paths must not be empty."}

    results = []
    for path in document_paths:
        filename = str(path).replace("\\", "/").split("/")[-1]
        results.append({
            "document": filename,
            "status": "queued",
            "note": (
                "Re-processing must be triggered via the upload endpoint. "
                "Ask the user to re-upload this document to refresh extracted facts."
            ),
        })

    return {
        "pipeline_trigger": "advisory",
        "documents": results,
        "action_required": (
            "To re-extract facts, the user should re-upload the affected documents "
            "through the upload interface. The extraction pipeline runs automatically on upload."
        ),
    }


def handle_query_facts(
    question: str,
    store: Any,
) -> dict[str, Any]:
    """
    Natural-language fact search: map question keywords → relevant fact paths.
    """
    if not question or not store:
        return {"found": False, "facts": [], "message": "No question or store provided."}

    q = question.lower()

    # Keyword → path-prefix mapping
    keyword_map: list[tuple[str, str]] = [
        ("water",                "rates.water"),
        ("council",              "rates.council"),
        ("land tax",             "rates.land_tax"),
        ("state revenue",        "rates.land_tax"),
        ("owners corporation",   "rates.owners_corporation"),
        ("oc fee",               "rates.owners_corporation"),
        ("owners corp",          "rates.owners_corporation"),
        ("planning",             "planning"),
        ("zone",                 "planning.zone"),
        ("overlay",              "planning.overlay"),
        ("bushfire",             "planning.bushfire"),
        ("responsible authority","planning.responsible_authority"),
        ("title",                "title"),
        ("volume",               "title.volume_folio"),
        ("folio",                "title.volume_folio"),
        ("encumbrance",          "title.encumbrances"),
        ("easement",             "title.easements"),
        ("covenant",             "title.covenants"),
        ("property",             "property"),
        ("address",              "property.address"),
    ]

    matched_prefixes: list[str] = []
    for keyword, prefix in keyword_map:
        if keyword in q and prefix not in matched_prefixes:
            matched_prefixes.append(prefix)

    if not matched_prefixes:
        # Broad search: return all priority facts
        matched_prefixes = ["rates", "planning", "title", "property"]

    all_results: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for prefix in matched_prefixes[:4]:
        paths = store.paths_matching(prefix)
        for path in paths[:10]:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            winner, conflict = store.get(path)
            if winner is None or winner.value in (None, ""):
                continue
            entry: dict[str, Any] = {
                "path": path,
                "value": winner.value,
                "confidence": round(winner.confidence, 3),
                "extractor": winner.extractor,
            }
            if winner.sources:
                src = winner.sources[0]
                entry["source"] = {"file": src.file, "page": src.page}
            if conflict and conflict.resolution == "review_required":
                entry["conflict"] = True
            all_results.append(entry)

    if not all_results:
        return {
            "found": False,
            "question": question,
            "message": "No matching facts found for this question.",
        }

    return {
        "found": True,
        "question": question,
        "matched_prefixes": matched_prefixes,
        "fact_count": len(all_results),
        "facts": all_results,
    }


def handle_check_sec32_coverage(store: Any) -> dict[str, Any]:
    """
    Return a structured coverage report for Sec 32 (Vendor's Statement) fields.
    """
    # Canonical Sec 32 field paths
    SEC32_FIELDS: list[tuple[str, str, str]] = [
        # (path, label, category)
        ("property.address",                    "Property address",              "property"),
        ("title.volume_folio",                  "Volume/Folio (title reference)","title"),
        ("title.registered_proprietors",        "Registered proprietors",        "title"),
        ("title.encumbrances",                  "Encumbrances",                  "title"),
        ("title.easements",                     "Easements",                     "title"),
        ("title.covenants",                     "Covenants",                     "title"),
        ("rates.council.authority_name",        "Council name",                  "rates"),
        ("rates.council.annual_amount",         "Annual council rates",           "rates"),
        ("rates.water.authority_name",          "Water authority name",           "rates"),
        ("rates.water.annual_amount",           "Annual water charge",            "rates"),
        ("rates.land_tax.authority_name",       "Land tax authority",             "rates"),
        ("rates.land_tax.amount",               "Land tax amount",               "rates"),
        ("rates.owners_corporation.exists",     "OC exists?",                    "oc"),
        ("rates.owners_corporation.annual_amount", "Annual OC fee",              "oc"),
        ("planning.zone",                       "Planning zone",                 "planning"),
        ("planning.overlay_names",              "Planning overlays",             "planning"),
        ("planning.bushfire_prone",             "Bushfire prone area",           "planning"),
        ("planning.responsible_authority",      "Responsible authority",         "planning"),
        ("planning.scheme_name",               "Planning scheme",               "planning"),
    ]

    filled: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    review_required: list[dict[str, Any]] = []

    for path, label, category in SEC32_FIELDS:
        winner, conflict = store.get(path)
        if winner is not None and winner.value not in (None, ""):
            entry = {
                "path": path,
                "label": label,
                "category": category,
                "value": winner.value,
                "confidence": round(winner.confidence, 3),
                "extractor": winner.extractor,
            }
            if conflict and conflict.resolution == "review_required":
                entry["conflict"] = True
                review_required.append(entry)
            else:
                filled.append(entry)
        else:
            missing.append({"path": path, "label": label, "category": category})

    total = len(SEC32_FIELDS)
    fill_pct = round(len(filled) / total * 100) if total else 0

    return {
        "coverage_pct": fill_pct,
        "total_fields": total,
        "filled_count": len(filled),
        "missing_count": len(missing),
        "review_required_count": len(review_required),
        "filled": filled,
        "missing": missing,
        "review_required": review_required,
        "summary": (
            f"{len(filled)}/{total} Sec 32 fields filled ({fill_pct}%). "
            f"{len(missing)} missing, {len(review_required)} need review."
        ),
    }


def handle_search_memory_tool(
    query: str,
    limit: int = 5,
    vector_memories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Return relevant past memory entries for the query.

    In the agentic loop, vector memories are pre-fetched and injected via the
    ``vector_memories`` parameter — no async DB call is made inside this sync handler.
    """
    limit = min(max(1, limit), 10)
    if not vector_memories:
        return {
            "found": False,
            "query": query,
            "results": [],
            "message": "No vector memories available for this session.",
        }

    # Simple keyword re-rank of pre-fetched memories
    q_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored: list[tuple[float, dict[str, Any]]] = []
    for mem in vector_memories:
        text = str(mem.get("content") or "").lower()
        m_tokens = set(re.findall(r"[a-z0-9]+", text))
        overlap = len(q_tokens & m_tokens)
        sim = float(mem.get("similarity") or 0.0)
        score = sim * 10 + overlap
        scored.append((score, mem))
    scored.sort(key=lambda x: x[0], reverse=True)

    results = [
        {
            "content": m.get("content", "")[:500],
            "similarity": m.get("similarity"),
            "created_at": m.get("created_at"),
            "session_id": m.get("session_id"),
        }
        for _, m in scored[:limit]
    ]

    return {
        "found": True,
        "query": query,
        "result_count": len(results),
        "results": results,
    }
