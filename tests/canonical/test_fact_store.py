"""Unit tests for the canonical FactStore + authority resolver."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.schemas import AuthorityRule, Fact, Source


def make_fact(
    path: str,
    value,
    *,
    extractor: str = "rule:test",
    confidence: float = 0.9,
    quote: str = "test quote",
    age_minutes: int = 0,
) -> Fact:
    return Fact(
        path=path,
        value=value,
        confidence=confidence,
        sources=[Source(file="test.pdf", quote=quote, quote_verified=True)],
        extractor=extractor,
        extracted_at=datetime.utcnow() - timedelta(minutes=age_minutes),
    )


class TestFactStoreBasics(unittest.TestCase):
    def test_add_then_get_returns_fact(self):
        store = FactStoreImpl()
        fact = make_fact("services.electricity.connected", True)
        store.add(fact)

        winner, conflict = store.get("services.electricity.connected")

        self.assertIsNotNone(winner)
        self.assertEqual(winner.value, True)
        self.assertIsNone(conflict, "single fact should produce no conflict record")

    def test_get_missing_path_returns_none(self):
        store = FactStoreImpl()
        winner, conflict = store.get("does.not.exist")
        self.assertIsNone(winner)
        self.assertIsNone(conflict)

    def test_get_value_convenience(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.council.annual_amount", "$1,234.56"))
        self.assertEqual(store.get_value("rates.council.annual_amount"), "$1,234.56")
        self.assertEqual(store.get_value("missing", default="N/A"), "N/A")

    def test_get_all_returns_every_fact_at_path(self):
        store = FactStoreImpl()
        store.add(make_fact("services.water.connected", True, extractor="rule:vendor_form_v2"))
        store.add(make_fact("services.water.connected", True, extractor="rule:water_authority_certificate"))

        all_facts = store.get_all("services.water.connected")
        self.assertEqual(len(all_facts), 2)

    def test_extractors_run_tracking(self):
        store = FactStoreImpl()
        store.add(make_fact("a.b", 1, extractor="rule:one"))
        store.add(make_fact("a.b", 2, extractor="rule:two"))
        store.add(make_fact("a.c", 3, extractor="rule:one"))  # duplicate extractor name
        self.assertEqual(set(store.extractors_run), {"rule:one", "rule:two"})

    def test_paths_and_paths_matching(self):
        store = FactStoreImpl()
        store.add(make_fact("rates.council.annual_amount", "$1"))
        store.add(make_fact("rates.water.annual_amount", "$2"))
        store.add(make_fact("services.electricity.connected", True))

        self.assertEqual(
            store.paths_matching("rates."),
            ["rates.council.annual_amount", "rates.water.annual_amount"],
        )
        self.assertEqual(len(store.paths()), 3)


class TestAuthorityResolution(unittest.TestCase):
    """Test the per-path authoritative-source ranking."""

    def test_authoritative_extractor_wins_over_vendor_form(self):
        """Planning cert should outrank vendor form for planning.bushfire_prone."""
        store = FactStoreImpl()
        store.add(make_fact(
            "planning.bushfire_prone",
            False,
            extractor="rule:vendor_form_v2",
            confidence=0.95,
        ))
        store.add(make_fact(
            "planning.bushfire_prone",
            True,
            extractor="rule:planning_certificate",
            confidence=0.85,  # lower confidence — but more authoritative
        ))

        winner, conflict = store.get("planning.bushfire_prone")
        self.assertIsNotNone(winner)
        self.assertEqual(winner.value, True, "planning certificate must win over vendor form")
        self.assertEqual(winner.extractor, "rule:planning_certificate")
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict.resolution, "authority_wins")
        self.assertIsNotNone(conflict.reason)

    def test_title_outranks_vendor_form_on_title_paths(self):
        store = FactStoreImpl()
        store.add(make_fact(
            "title.volume_folio",
            "VOLUME 1 FOLIO 1",
            extractor="rule:vendor_form_v2",
            confidence=0.99,
        ))
        store.add(make_fact(
            "title.volume_folio",
            "VOLUME 12345 FOLIO 678",
            extractor="rule:vic_title_v1",
            confidence=0.80,
        ))

        winner, _ = store.get("title.volume_folio")
        self.assertEqual(winner.value, "VOLUME 12345 FOLIO 678")
        self.assertEqual(winner.extractor, "rule:vic_title_v1")

    def test_review_required_when_no_authoritative_extractor_and_close_confidence(self):
        """Catch-all path with two facts at almost-equal confidence escalates."""
        store = FactStoreImpl()
        store.add(make_fact("custom.thing", "A", extractor="rule:source_a", confidence=0.80))
        store.add(make_fact("custom.thing", "B", extractor="rule:source_b", confidence=0.81))

        winner, conflict = store.get("custom.thing")
        self.assertIsNone(winner, "tied confidences must escalate, not silently pick")
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict.resolution, "review_required")

    def test_highest_confidence_wins_when_margin_is_clear(self):
        store = FactStoreImpl()
        store.add(make_fact("custom.thing", "A", extractor="rule:source_a", confidence=0.60))
        store.add(make_fact("custom.thing", "B", extractor="rule:source_b", confidence=0.95))

        winner, conflict = store.get("custom.thing")
        self.assertIsNotNone(winner)
        self.assertEqual(winner.value, "B")
        self.assertEqual(conflict.resolution, "authority_wins")

    def test_tied_top_authority_falls_back_to_confidence(self):
        """Two facts both from the top-ranked extractor pattern → confidence breaks the tie."""
        store = FactStoreImpl()
        store.add(make_fact(
            "planning.zone",
            "GRZ - General Residential Zone",
            extractor="rule:planning_certificate",
            confidence=0.70,
        ))
        store.add(make_fact(
            "planning.zone",
            "NRZ - Neighbourhood Residential Zone",
            extractor="ai:doc_extractor:planning_certificate",
            confidence=0.85,
        ))
        # Both match `rule:planning_certificate*` at rank 0 / `ai:doc_extractor:planning_certificate*` at rank 1
        # The rule order in DEFAULT_AUTHORITY_RULES means rule:* outranks ai:* — that fact wins by rank, not confidence
        winner, _ = store.get("planning.zone")
        self.assertEqual(winner.extractor, "rule:planning_certificate")
        self.assertEqual(winner.value, "GRZ - General Residential Zone")

    def test_identical_top_authority_facts_do_not_escalate(self):
        """Duplicate authoritative docs with the same value should resolve cleanly."""
        store = FactStoreImpl()
        store.add(make_fact(
            "rates.water.authority_name",
            "Greater Western Water",
            extractor="rule:water_authority_certificate_v1",
            confidence=0.99,
        ))
        store.add(make_fact(
            "rates.water.authority_name",
            "Greater Western Water",
            extractor="rule:water_authority_certificate_v1",
            confidence=0.99,
        ))
        store.add(make_fact(
            "rates.water.authority_name",
            "Greater West Water",
            extractor="rule:vendor_form_v2",
            confidence=0.97,
        ))

        winner, conflict = store.get("rates.water.authority_name")
        self.assertIsNotNone(winner)
        self.assertEqual(winner.value, "Greater Western Water")
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict.resolution, "authority_wins")

    def test_glob_extractor_pattern_versioning(self):
        """rule:vic_title_v3 should still match rule:vic_title* pattern."""
        store = FactStoreImpl()
        store.add(make_fact(
            "title.registered_proprietor",
            "JOHN SMITH",
            extractor="rule:vic_title_v3",  # newer version
            confidence=0.92,
        ))
        store.add(make_fact(
            "title.registered_proprietor",
            "WRONG",
            extractor="rule:vendor_form_v2",
            confidence=0.99,
        ))

        winner, _ = store.get("title.registered_proprietor")
        self.assertEqual(winner.value, "JOHN SMITH")


class TestConflictDetection(unittest.TestCase):
    def test_conflicts_lists_only_paths_with_multiple_facts(self):
        store = FactStoreImpl()
        store.add(make_fact("a.b", 1))
        store.add(make_fact("c.d", 1))
        store.add(make_fact("c.d", 2))  # conflict here

        conflicts = store.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].path, "c.d")

    def test_unresolved_conflicts_only_returns_review_required(self):
        store = FactStoreImpl()
        # Resolved conflict (planning cert wins over vendor)
        store.add(make_fact("planning.zone", "GRZ", extractor="rule:vendor_form_v2", confidence=0.9))
        store.add(make_fact("planning.zone", "NRZ", extractor="rule:planning_certificate", confidence=0.9))
        # Unresolved conflict (catch-all + tied confidence)
        store.add(make_fact("misc.thing", "X", extractor="rule:a", confidence=0.81))
        store.add(make_fact("misc.thing", "Y", extractor="rule:b", confidence=0.83))

        unresolved = store.unresolved_conflicts()
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].path, "misc.thing")


class TestCustomAuthorityRules(unittest.TestCase):
    def test_user_supplied_rules_override_defaults(self):
        custom_rules = [
            AuthorityRule(
                path_pattern="weird.*",
                authoritative_extractors=["rule:special_extractor"],
                on_unresolved="review",
            ),
            AuthorityRule(
                path_pattern="*",
                authoritative_extractors=[],
                on_unresolved="highest_confidence",
            ),
        ]
        store = FactStoreImpl(authority_rules=custom_rules)
        store.add(make_fact("weird.thing", "A", extractor="rule:plain", confidence=0.99))
        store.add(make_fact("weird.thing", "B", extractor="rule:special_extractor", confidence=0.50))

        winner, _ = store.get("weird.thing")
        self.assertEqual(winner.value, "B", "custom authority rule must win over confidence")


if __name__ == "__main__":
    unittest.main()
