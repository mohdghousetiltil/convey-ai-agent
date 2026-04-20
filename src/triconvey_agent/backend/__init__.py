"""Backend service layer for production-oriented app integration."""

__all__ = ["autofill_run", "build_review_run", "load_run_payload", "save_review_answers"]


def autofill_run(*args, **kwargs):
    from triconvey_agent.backend.service import autofill_run as _autofill_run

    return _autofill_run(*args, **kwargs)


def build_review_run(*args, **kwargs):
    from triconvey_agent.backend.service import build_review_run as _build_review_run

    return _build_review_run(*args, **kwargs)


def load_run_payload(*args, **kwargs):
    from triconvey_agent.backend.service import load_run_payload as _load_run_payload

    return _load_run_payload(*args, **kwargs)


def save_review_answers(*args, **kwargs):
    from triconvey_agent.backend.service import save_review_answers as _save_review_answers

    return _save_review_answers(*args, **kwargs)
