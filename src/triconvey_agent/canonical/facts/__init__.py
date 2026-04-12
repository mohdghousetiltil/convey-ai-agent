"""Brain A — fact store, authority resolver, and conflict detection."""
from triconvey_agent.canonical.facts.authority import (
    DEFAULT_AUTHORITY_RULES,
    resolve_facts,
)
from triconvey_agent.canonical.facts.store import FactStoreImpl

__all__ = [
    "FactStoreImpl",
    "DEFAULT_AUTHORITY_RULES",
    "resolve_facts",
]
