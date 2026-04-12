"""Brain B — the Question Router.

Takes a FactStore (Brain A's output) plus a Question registry and produces
one AnswerObject per question. Strategies:

- deterministic : look up fact_paths in the store, apply value_transform
- grounded_ai   : uses AI, but only from extracted evidence quotes
- human_review  : always escalates
- policy_default: relies on ClientPolicy (Brain C); without one, escalates

The router never invents values, never mutates Facts, and never decides
presentation — it only answers the objective question.
"""
from triconvey_agent.canonical.router.router import (
    answer_all_questions,
    answer_question,
)

__all__ = ["answer_question", "answer_all_questions"]
