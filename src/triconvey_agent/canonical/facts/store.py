"""FactStore implementation — append-only canonical truth.

Usage
-----
    store = FactStoreImpl()
    store.add(Fact(path="title.volume_folio", value="VOLUME 12345 FOLIO 678", ...))
    store.add(Fact(path="title.volume_folio", value="VOLUME 12345 FOLIO 999", ...))

    fact, conflict = store.get("title.volume_folio")
    # fact   — winning Fact, resolved via DEFAULT_AUTHORITY_RULES
    # conflict — Conflict record if there were multiple candidates

    all_at_path = store.get_all("title.volume_folio")  # list[Fact]
    all_unresolved = store.unresolved_conflicts()      # list[Conflict]

Design
------
- Append-only: extractors call .add() freely. The store never overwrites.
- Resolution happens at *query time*, not at write time. This means a
  later extractor can add evidence that changes the winning fact without
  the earlier extractor knowing or caring.
- The store wraps the pydantic FactStore schema for serialization and
  exposes query helpers on top.
"""
from __future__ import annotations

from typing import Iterable

from triconvey_agent.canonical.facts.authority import (
    DEFAULT_AUTHORITY_RULES,
    resolve_facts,
)
from triconvey_agent.canonical.schemas import (
    AuthorityRule,
    Conflict,
    DocInfo,
    Fact,
    FactStore,
)


class FactStoreImpl:
    """Append-only canonical fact store with authority-based resolution."""

    def __init__(
        self,
        authority_rules: list[AuthorityRule] | None = None,
    ) -> None:
        self._store = FactStore()
        self._rules = authority_rules or DEFAULT_AUTHORITY_RULES

    # ---- mutation (append-only) -----------------------------------------

    def add(self, fact: Fact) -> None:
        """Append one Fact. Never overwrites existing facts."""
        self._store.facts.setdefault(fact.path, []).append(fact)
        if fact.extractor not in self._store.extractors_run:
            self._store.extractors_run.append(fact.extractor)

    def add_many(self, facts: Iterable[Fact]) -> None:
        for fact in facts:
            self.add(fact)

    def register_document(self, doc: DocInfo) -> None:
        """Record an ingested document for the audit trail."""
        self._store.documents.append(doc)

    # ---- query ----------------------------------------------------------

    def get(self, path: str) -> tuple[Fact | None, Conflict | None]:
        """Return (winning fact, conflict record if any).

        - If no fact exists at this path: (None, None)
        - If exactly one fact: (that fact, None)
        - If multiple facts: resolved via authority rules.
          Returns (winner, conflict) where winner may be None if
          escalated to review.
        """
        facts = self._store.facts.get(path, [])
        return resolve_facts(path, facts, self._rules)

    def get_value(self, path: str, default: object = None) -> object:
        """Convenience: return just the resolved value, or default."""
        fact, _ = self.get(path)
        return fact.value if fact is not None else default

    def get_all(self, path: str) -> list[Fact]:
        """Return every Fact ever asserted at this path (no resolution)."""
        return list(self._store.facts.get(path, []))

    def has(self, path: str) -> bool:
        return bool(self._store.facts.get(path))

    def paths(self) -> list[str]:
        """Every path that has at least one fact."""
        return sorted(self._store.facts.keys())

    def paths_matching(self, prefix: str) -> list[str]:
        """All paths starting with the given prefix (e.g. 'rates.council')."""
        return sorted(p for p in self._store.facts if p.startswith(prefix))

    # ---- conflicts ------------------------------------------------------

    def conflicts(self) -> list[Conflict]:
        """Every path with 2+ facts, with the resolver's verdict applied."""
        results: list[Conflict] = []
        for path, facts in self._store.facts.items():
            if len(facts) < 2:
                continue
            _, conflict = resolve_facts(path, facts, self._rules)
            if conflict is not None:
                results.append(conflict)
        return results

    def unresolved_conflicts(self) -> list[Conflict]:
        """Conflicts that escalated to human review."""
        return [c for c in self.conflicts() if c.resolution == "review_required"]

    # ---- inspection / serialization -------------------------------------

    @property
    def documents(self) -> list[DocInfo]:
        return list(self._store.documents)

    @property
    def extractors_run(self) -> list[str]:
        return list(self._store.extractors_run)

    def fact_count(self) -> int:
        return sum(len(v) for v in self._store.facts.values())

    def to_schema(self) -> FactStore:
        """Return the underlying pydantic FactStore for serialization."""
        return self._store.model_copy(deep=True)

    def to_dict(self) -> dict:
        return self._store.model_dump(mode="json")

    @classmethod
    def from_schema(
        cls,
        store: FactStore,
        authority_rules: list[AuthorityRule] | None = None,
    ) -> "FactStoreImpl":
        impl = cls(authority_rules=authority_rules)
        impl._store = store.model_copy(deep=True)
        return impl
