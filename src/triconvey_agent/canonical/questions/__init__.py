"""Brain B — Question registry and router (loader only at step 2)."""
from triconvey_agent.canonical.questions.loader import (
    QuestionLoadError,
    load_question_registry,
    load_questions_from_yaml,
)

__all__ = [
    "load_questions_from_yaml",
    "load_question_registry",
    "QuestionLoadError",
]
