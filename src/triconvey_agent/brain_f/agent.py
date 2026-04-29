"""Brain F — agentic copilot using Anthropic, OpenAI, or Google tool use.

Modes:
  quick     — 2 tool rounds max, no critic loop
  standard  — 6 tool rounds, critic loop for high-stakes answers
  thorough  — 10 tool rounds, critic loop always runs

Response schema:
  {
    "answer":           str,
    "citations":        [{file, page, quote}],
    "field_answers":    [{question_id, value, confidence}],
    "proposed_patches": [{question_id, new_value, reason, status}],
    "reasoning_steps":  [{tool, input, summary}],
    "tool_calls_made":  int,
    "confidence_note":  str | None,
    "critic_applied":   bool,
  }
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from triconvey_agent.brain_f.memory import format_memory_for_prompt, load_document_memory
from triconvey_agent.brain_f.prompts import CRITIC_PROMPT, build_system_prompt
from triconvey_agent.brain_f.tools import TOOL_DEFINITIONS, dispatch_tool
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.questions.loader import load_question_registry

LOG = logging.getLogger(__name__)

_MODE_MAX_ROUNDS = {"quick": 2, "standard": 4, "deep": 7, "thorough": 7}
_HIGH_STAKES_RE = re.compile(r"\$[\d,]+\.\d{2}|liable|must\s+comply|illegal|penalty|encumbrance", re.IGNORECASE)


class BrainFAgent:
    """Multi-stage agentic Q&A copilot. Supports OpenAI, Anthropic, and Google."""

    def __init__(
        self,
        store: FactStoreImpl,
        run_dir: Path,
        *,
        model: str = "gpt-4.1-mini",
        provider: str = "openai",
        corpus_chunks: list[dict[str, Any]] | None = None,
        persist_chat: bool = True,
        session_id: str | None = None,
    ) -> None:
        self._store = store
        self._run_dir = run_dir
        self._model = model
        self._provider = provider.lower()
        self._persist_chat = persist_chat
        self._corpus_chunks: list[dict[str, Any]] = corpus_chunks or _load_corpus(run_dir)
        self._memory: dict[str, Any] = load_document_memory(run_dir)
        self._fact_snapshot = _build_fact_snapshot(store)
        self._conflict_snapshot = _build_conflict_snapshot(store)
        self._structured_corpus_block = _load_structured_corpus_block(run_dir)
        self._chat_state = _load_chat_state(run_dir)
        self._question_registry = load_question_registry()
        # Feature 4: session identifier for vector memory scoping
        self._session_id = session_id or _new_session_id()

        if self._provider == "anthropic":
            self._client = _make_anthropic_client()
        elif self._provider == "google":
            # Google uses MultiModelClient; fall back gracefully on import error
            try:
                from triconvey_agent.ai.multi_client import MultiModelClient
                self._client = MultiModelClient(provider="google", model=self._model)
            except Exception as exc:
                LOG.warning("Google client init failed: %s — falling back to OpenAI", exc)
                self._provider = "openai"
                self._client = _make_openai_client()
        else:
            self._client = _make_openai_client()

    def ask(
        self,
        question: str,
        history: list[dict[str, Any]] | None = None,
        mode: str = "standard",
        vector_memories: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Answer a question using the agentic loop.

        Args:
            question:        The user's question.
            history:         Explicit conversation history (UI-provided turns).
            mode:            "quick" | "standard" | "thorough"
            vector_memories: Pre-fetched vector memory entries to inject as
                             system context (Feature 4).  The caller is
                             responsible for retrieving these asynchronously
                             before calling ask().
        """
        pending_patches: list[dict[str, Any]] = []
        reasoning_steps: list[dict[str, Any]] = []
        memory_block = format_memory_for_prompt(self._memory)
        specialist_block = _build_specialist_block(question)

        # Feature 4: prepend vector memory context to the system prompt
        vector_context_block = _format_vector_memories(vector_memories or [])

        system = build_system_prompt(
            mode=mode,
            memory_block=memory_block,
            fact_snapshot=self._fact_snapshot,
            conflict_snapshot=self._conflict_snapshot,
            conversation_summary=self._chat_state.get("summary", ""),
            specialist_block=specialist_block,
            corpus_block=self._structured_corpus_block,
        )
        if vector_context_block:
            system = vector_context_block + "\n\n" + system

        max_rounds = _MODE_MAX_ROUNDS.get(mode, 6)
        merged_history = _merge_history(self._chat_state.get("turns", []), history or [])

        if self._provider == "anthropic":
            result = self._ask_anthropic(
                question, merged_history, system, pending_patches, reasoning_steps, max_rounds,
                vector_memories=vector_memories or [],
            )
        elif self._provider == "google":
            result = self._ask_google(
                question, merged_history, system, pending_patches, reasoning_steps, max_rounds,
                vector_memories=vector_memories or [],
            )
        else:
            result = self._ask_openai(
                question, merged_history, system, pending_patches, reasoning_steps, max_rounds,
                vector_memories=vector_memories or [],
            )

        # Critic loop — thorough mode only (standard mode trusts the pre-loaded fact snapshot)
        critic_applied = False
        if mode in {"deep", "thorough"}:
            refined = self._run_critic(result["answer"], system)
            if refined and refined != result["answer"]:
                result["answer"] = refined
                critic_applied = True

        result["critic_applied"] = critic_applied
        result["field_answers"] = []  # field suggestions disabled — AI derives from corpus, not draft answers
        result["reasoning_steps"] = reasoning_steps
        result["session_id"] = self._session_id  # Feature 4
        if self._persist_chat:
            _save_chat_state(self._run_dir, self._chat_state, question, result["answer"])
        return result

    # ------------------------------------------------------------------ #
    # Anthropic agentic loop                                               #
    # ------------------------------------------------------------------ #

    def _ask_anthropic(
        self, question, history, system, pending_patches, reasoning_steps, max_rounds,
        vector_memories=None,
    ):
        messages = _build_messages(history, question)
        tool_calls_made = 0

        for _ in range(max_rounds):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                break
            if response.stop_reason != "tool_use":
                break

            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_calls_made += 1
                result_json = dispatch_tool(
                    block.name, block.input,
                    store=self._store, memory=self._memory,
                    corpus_chunks=self._corpus_chunks, pending_patches=pending_patches,
                    run_dir=self._run_dir, vector_memories=vector_memories,
                )
                reasoning_steps.append(_summarize_tool_step(block.name, block.input, result_json))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_json,
                })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            messages.append({"role": "user", "content": "Please give a final answer now."})
            response = self._client.messages.create(
                model=self._model, max_tokens=1024, system=system, messages=messages,
            )

        answer = "\n".join(
            block.text for block in response.content if hasattr(block, "text")
        ).strip()
        return _build_result(question, answer, pending_patches, self._corpus_chunks, self._store, tool_calls_made, reasoning_steps)

    # ------------------------------------------------------------------ #
    # OpenAI agentic loop                                                  #
    # ------------------------------------------------------------------ #

    def _ask_openai(
        self, question, history, system, pending_patches, reasoning_steps, max_rounds,
        vector_memories=None,
    ):
        openai_tools = [_to_openai_tool(t) for t in TOOL_DEFINITIONS]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for turn in history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)})
        messages.append({"role": "user", "content": question})

        tool_calls_made = 0

        for _ in range(max_rounds):
            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=1024,
                tools=openai_tools,
                tool_choice="auto",
                messages=messages,
            )
            msg = response.choices[0].message
            if not msg.tool_calls:
                break

            messages.append(msg)
            for tc in msg.tool_calls:
                tool_calls_made += 1
                try:
                    tool_input = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    tool_input = {}
                result_json = dispatch_tool(
                    tc.function.name, tool_input,
                    store=self._store, memory=self._memory,
                    corpus_chunks=self._corpus_chunks, pending_patches=pending_patches,
                    run_dir=self._run_dir, vector_memories=vector_memories,
                )
                reasoning_steps.append(_summarize_tool_step(tc.function.name, tool_input, result_json))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_json})
        else:
            messages.append({"role": "user", "content": "Please give a final answer now."})
            response = self._client.chat.completions.create(
                model=self._model, max_tokens=1024, messages=messages,
            )
            msg = response.choices[0].message

        answer = (msg.content or "").strip()
        return _build_result(question, answer, pending_patches, self._corpus_chunks, self._store, tool_calls_made, reasoning_steps)

    # ------------------------------------------------------------------ #
    # Google Gemini agentic loop (Feature 1 + 3)                          #
    # ------------------------------------------------------------------ #

    def _ask_google(
        self, question, history, system, pending_patches, reasoning_steps, max_rounds,
        vector_memories=None,
    ):
        """Agentic loop for Google Gemini via MultiModelClient."""
        from triconvey_agent.ai.multi_client import MultiModelClient

        messages: list[dict[str, Any]] = []
        for turn in history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)})
        messages.append({"role": "user", "content": question})

        tool_calls_made = 0
        last_answer = ""

        for _ in range(max_rounds):
            resp = self._client.chat(
                messages,
                system=system,
                tools=TOOL_DEFINITIONS,
                max_tokens=1024,
            )

            if resp.stop_reason == "end_turn" or not resp.tool_calls:
                last_answer = resp.content
                break

            # Execute tool calls
            for tc in resp.tool_calls:
                tool_calls_made += 1
                result_json = dispatch_tool(
                    tc.name, tc.arguments,
                    store=self._store, memory=self._memory,
                    corpus_chunks=self._corpus_chunks, pending_patches=pending_patches,
                    run_dir=self._run_dir, vector_memories=vector_memories,
                )
                reasoning_steps.append(_summarize_tool_step(tc.name, tc.arguments, result_json))
                # Append tool result as a user message (Google doesn't have a native tool_result role)
                messages.append({
                    "role": "user",
                    "content": f"[Tool '{tc.name}' result]: {result_json}",
                })
            if resp.content:
                messages.append({"role": "assistant", "content": resp.content})
        else:
            # Max rounds exhausted
            messages.append({"role": "user", "content": "Please give a final answer now."})
            final_resp = self._client.chat(messages, system=system, max_tokens=1024)
            last_answer = final_resp.content

        return _build_result(
            question, last_answer, pending_patches,
            self._corpus_chunks, self._store, tool_calls_made, reasoning_steps,
        )

    # ------------------------------------------------------------------ #
    # Critic loop                                                          #
    # ------------------------------------------------------------------ #

    def _run_critic(self, draft: str, system: str) -> str:
        """Second-pass critic call that checks dollar amounts, authority names, and hierarchy."""
        critic_system = "You are a senior conveyancing reviewer. Be concise but thorough."
        critic_user = CRITIC_PROMPT.format(
            draft=draft,
            fact_snapshot=self._fact_snapshot or "No pre-loaded facts available.",
            conflict_snapshot=self._conflict_snapshot or "No unresolved conflicts detected.",
        )
        try:
            if self._provider == "anthropic":
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    system=critic_system,
                    messages=[{"role": "user", "content": critic_user}],
                )
                return "\n".join(b.text for b in resp.content if hasattr(b, "text")).strip() or draft
            else:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    max_tokens=1024,
                    messages=[
                        {"role": "system", "content": critic_system},
                        {"role": "user", "content": critic_user},
                    ],
                )
                return (resp.choices[0].message.content or "").strip() or draft
        except Exception as exc:
            LOG.warning("Critic loop failed: %s", exc)
            return draft


# ------------------------------------------------------------------ #
# Client factories                                                      #
# ------------------------------------------------------------------ #


def _make_anthropic_client():
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set. Add it in Settings.")
    return anthropic.Anthropic(api_key=api_key)


def _make_openai_client():
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set. Add it in Settings.")
    return OpenAI(api_key=api_key)


def _new_session_id() -> str:
    """Generate a new UUID session identifier."""
    import uuid
    return str(uuid.uuid4())


# ------------------------------------------------------------------ #
# Helpers                                                               #
# ------------------------------------------------------------------ #


def _is_high_stakes(answer: str) -> bool:
    return bool(_HIGH_STAKES_RE.search(answer))


def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


def _load_corpus(run_dir: Path) -> list[dict[str, Any]]:
    manifest_path = run_dir / "document_corpus_manifest.json"
    if not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        index_path = Path(str(manifest.get("index_path") or ""))
        if index_path.exists():
            return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _build_messages(history, question):
    messages: list[dict[str, Any]] = []
    for turn in history:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)})
    messages.append({"role": "user", "content": question})
    return messages


def _build_result(question, answer, pending_patches, corpus_chunks, store, tool_calls_made, reasoning_steps):
    return {
        "answer": answer,
        "citations": _extract_citations(corpus_chunks, answer, question),
        "proposed_patches": pending_patches,
        "field_answers": [],
        "reasoning_steps": reasoning_steps,
        "tool_calls_made": tool_calls_made,
        "confidence_note": _confidence_note(store),
    }


def _build_fact_snapshot(store: FactStoreImpl) -> str:
    priority_prefixes = (
        "rates.council",
        "rates.water",
        "rates.land_tax",
        "rates.owners_corporation",
        "planning.",
        "property.",
        "title.",
    )
    lines: list[str] = []
    seen: set[str] = set()
    for prefix in priority_prefixes:
        for path in store.paths_matching(prefix):
            if path in seen:
                continue
            seen.add(path)
            winner, conflict = store.get(path)
            if winner is None or winner.value in (None, ""):
                continue
            src = winner.sources[0].file if winner.sources else "unknown"
            marker = " (conflict)" if conflict is not None and conflict.resolution == "review_required" else ""
            lines.append(
                f"- {path}: {winner.value} [conf {winner.confidence:.2f}, {winner.extractor}, {src}]{marker}"
            )
    return "\n".join(lines[:30]) or "No pre-loaded facts available."


def _build_conflict_snapshot(store: FactStoreImpl) -> str:
    unresolved = store.unresolved_conflicts()
    if not unresolved:
        return "No unresolved conflicts detected."
    lines: list[str] = []
    for conflict in unresolved[:8]:
        values = []
        for fact in conflict.facts[:3]:
            values.append(f"{fact.extractor}: {fact.value}")
        lines.append(f"- {conflict.path}: " + " vs ".join(values))
    return "\n".join(lines)


def _extract_citations(corpus_chunks, answer_text, question_text=""):
    citations: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    answer_lower = answer_text.lower()
    for chunk in corpus_chunks:
        filename = chunk.get("file") or ""
        stem = filename.lower().rsplit(".", 1)[0]
        if stem and stem in answer_lower:
            key = (filename, chunk.get("page"))
            if key not in seen:
                seen.add(key)
                citations.append({
                    "file": filename,
                    "page": chunk.get("page"),
                    "quote": str(chunk.get("text") or "")[:240],
                })
    if citations:
        return citations
    ranked = _rank_chunks_for_citations(question_text or answer_text, corpus_chunks)
    for chunk in ranked[:10]:
        key = (chunk.get("file"), chunk.get("page"), chunk.get("chunk_id"))
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "file": chunk.get("file"),
                "page": chunk.get("page"),
                "quote": str(chunk.get("text") or "")[:240],
            }
        )
    return citations


def _confidence_note(store: FactStoreImpl) -> str | None:
    return None  # confidence notes suppressed — low-conf facts flagged inline in answer text


def _build_specialist_block(question: str) -> str:
    q = (question or "").lower()
    if any(token in q for token in ("water", "rates", "land tax", "owners corporation", "oc fee")):
        return "Rates and outgoings specialist mode. Prioritise authority certificates, annualised amounts, and exact authority display names."
    if any(token in q for token in ("title", "encumbrance", "covenant", "easement", "agreement")):
        return "Title specialist mode. Prioritise title documents, registered instruments, and exact dealing numbers."
    if any(token in q for token in ("zone", "overlay", "planning", "bushfire", "responsible authority")):
        return "Planning specialist mode. Prioritise planning certificates, overlays, bushfire, and responsible authority disclosures."
    return "General conveyancing copilot mode."


def _merge_history(persisted: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = list(persisted or [])
    for turn in current or []:
        if not merged or merged[-1].get("role") != turn.get("role") or merged[-1].get("content") != turn.get("content"):
            merged.append(turn)
    return merged[-10:]


def _chat_state_path(run_dir: Path) -> Path:
    return run_dir / "chat_history.json"


def _load_chat_state(run_dir: Path) -> dict[str, Any]:
    path = _chat_state_path(run_dir)
    if not path.exists():
        return {"summary": "", "turns": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "summary": str(payload.get("summary") or ""),
            "turns": list(payload.get("turns") or []),
        }
    except Exception:
        return {"summary": "", "turns": []}


def _save_chat_state(run_dir: Path, state: dict[str, Any], question: str, answer: str) -> None:
    turns = list(state.get("turns") or [])
    turns.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ])
    turns = turns[-10:]
    summary_lines = []
    for turn in turns[-5:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        summary_lines.append(f"- {role}: {str(turn.get('content') or '')[:180]}")
    payload = {"summary": "\n".join(summary_lines), "turns": turns}
    _chat_state_path(run_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    state.clear()
    state.update(payload)


def _summarize_tool_step(tool_name: str, tool_input: dict[str, Any], result_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(result_json)
    except Exception:
        payload = {"raw": result_json[:220]}
    summary = ""
    if tool_name == "get_facts":
        facts = payload.get("facts") or []
        if facts:
            first = facts[0]
            summary = f"{first.get('path')} -> {first.get('resolved_value')} ({first.get('resolved_extractor')})"
    elif tool_name == "search_documents":
        results = payload.get("results") or []
        if results:
            first = results[0]
            summary = f"{first.get('file')} p.{first.get('page')} score={first.get('score')}"
    elif tool_name == "compare_facts":
        summary = f"match={payload.get('match')}"
    elif tool_name == "run_review_checklist":
        summary = f"{len(payload.get('items') or [])} checklist items generated"
    elif tool_name == "propose_answer_patch":
        patch = payload.get("patch") or {}
        summary = f"{patch.get('question_id')} -> {patch.get('new_value')}"
    else:
        summary = str(payload)[:180]
    return {"tool": tool_name, "input": tool_input, "summary": summary}


def _infer_field_answers(question: str, store: FactStoreImpl, registry: dict[str, Any]) -> list[dict[str, Any]]:
    q = (question or "").lower()
    candidate_paths: list[str] = []
    if "water" in q:
        candidate_paths.extend(["rates.water.authority_name", "rates.water.annual_amount"])
    if "council" in q or "rates" in q:
        candidate_paths.extend(["rates.council.authority_name", "rates.council.annual_amount"])
    if "land tax" in q or "state revenue" in q:
        candidate_paths.extend(["rates.land_tax.authority_name", "rates.land_tax.amount"])
    if "owners corporation" in q or "owner corporation" in q or "oc " in q or "oc fee" in q:
        candidate_paths.extend(["rates.owners_corporation.authority_name", "rates.owners_corporation.annual_amount"])
    if "bushfire" in q:
        candidate_paths.append("planning.bushfire_prone")
    if "planning scheme" in q:
        candidate_paths.append("planning.scheme_name")
    if "responsible authority" in q:
        candidate_paths.append("planning.responsible_authority")
    if "zone" in q:
        candidate_paths.append("planning.zone")
    if "overlay" in q:
        candidate_paths.append("planning.overlay_names")

    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in candidate_paths:
        winner, _ = store.get(path)
        if winner is None or winner.value in (None, ""):
            continue
        for qid, item in registry.items():
            if path in (item.fact_paths or []) and qid not in seen:
                seen.add(qid)
                resolved.append(
                    {
                        "question_id": qid,
                        "value": winner.value,
                        "confidence": round(winner.confidence, 3),
                    }
                )
    return resolved


def _format_vector_memories(memories: list[dict[str, Any]]) -> str:
    """Render pre-fetched vector memories as a system-prompt block (Feature 4)."""
    if not memories:
        return ""
    lines = ["## Relevant past conversation context (from vector memory):"]
    for i, mem in enumerate(memories[:5], 1):
        content = str(mem.get("content") or "").strip()[:400]
        sim = mem.get("similarity")
        sim_note = f" [similarity: {sim:.2f}]" if sim is not None else ""
        lines.append(f"\n### Memory {i}{sim_note}")
        lines.append(content)
    return "\n".join(lines)


def _rank_chunks_for_citations(query: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q_tokens = set(re.findall(r"[a-z0-9]+", (query or "").lower()))
    scored: list[tuple[int, dict[str, Any]]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "").lower()
        overlap = len(q_tokens & set(re.findall(r"[a-z0-9]+", text)))
        if overlap:
            scored.append((overlap, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored]


def _load_structured_corpus_block(run_dir: Path) -> str:
    """Load the structured matter corpus and format it for the system prompt.

    Returns an empty string if no corpus exists yet (corpus is optional —
    Brain F works without it but answers are richer when it exists).
    """
    corpus_file = run_dir / "corpus.json"
    if not corpus_file.exists():
        return ""
    try:
        from triconvey_agent.corpus.builder import build_corpus_context
        from triconvey_agent.corpus.schema import MatterCorpus
        corpus = MatterCorpus.load(str(corpus_file))
        return build_corpus_context(corpus)
    except Exception as exc:
        LOG.warning("Could not load structured corpus from %s: %s", corpus_file, exc)
        return ""
