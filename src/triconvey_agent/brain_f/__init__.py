"""Brain F — multi-stage agentic copilot for conveyancing Q&A.

Exposes:
  BrainFAgent   — agentic loop using Anthropic tool use
  build_document_memory  — generate per-doc summaries at upload time
"""
from triconvey_agent.brain_f.agent import BrainFAgent
from triconvey_agent.brain_f.memory import build_document_memory, load_document_memory

__all__ = ["BrainFAgent", "build_document_memory", "load_document_memory"]
