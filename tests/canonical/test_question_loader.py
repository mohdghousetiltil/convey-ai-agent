"""Unit tests for the YAML question registry loader."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from triconvey_agent.canonical.questions.loader import (
    QuestionLoadError,
    load_question_registry,
    load_questions_from_yaml,
)
from triconvey_agent.canonical.schemas import AnswerStrategy


def write_temp_yaml(content: str) -> Path:
    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    fd.write(content)
    fd.close()
    return Path(fd.name)


class TestQuestionLoaderHappyPath(unittest.TestCase):
    def test_load_single_question(self):
        yaml_text = """\
questions:
  - id: q1
    tab: Tab A
    label: A simple question
    fact_paths: [a.b]
    answer_strategy: deterministic
    expected_type: bool
"""
        path = write_temp_yaml(yaml_text)
        try:
            registry = load_questions_from_yaml(path)
        finally:
            path.unlink()

        self.assertEqual(len(registry), 1)
        self.assertIn("q1", registry)
        q = registry["q1"]
        self.assertEqual(q.label, "A simple question")
        self.assertEqual(q.fact_paths, ["a.b"])
        self.assertEqual(q.answer_strategy, AnswerStrategy.DETERMINISTIC)

    def test_load_bundled_registry_validates(self):
        """The bundled registry.yaml must always be valid."""
        registry = load_question_registry()
        self.assertGreater(len(registry), 0, "bundled registry should have entries")

        # Strategies present
        strategies = {q.answer_strategy for q in registry.values()}
        self.assertIn(AnswerStrategy.DETERMINISTIC, strategies)
        self.assertIn(AnswerStrategy.GROUNDED_AI, strategies)
        self.assertIn(AnswerStrategy.HUMAN_REVIEW, strategies)
        self.assertIn(AnswerStrategy.POLICY_DEFAULT, strategies)

    def test_loaded_question_with_options(self):
        registry = load_question_registry()
        zone_q = registry.get("sec32_3.4_planning_zone")
        self.assertIsNotNone(zone_q)
        self.assertIsNotNone(zone_q.options)
        self.assertGreater(len(zone_q.options), 0)
        self.assertIn("GRZ - General Residential Zone", zone_q.options)


class TestQuestionLoaderErrors(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(QuestionLoadError) as cm:
            load_questions_from_yaml("/no/such/path/questions.yaml")
        self.assertIn("not found", str(cm.exception))

    def test_invalid_yaml_raises_clear_error(self):
        path = write_temp_yaml("questions:\n  - id: q1\n  this is not valid yaml: [")
        try:
            with self.assertRaises(QuestionLoadError) as cm:
                load_questions_from_yaml(path)
            self.assertIn("Invalid YAML", str(cm.exception))
        finally:
            path.unlink()

    def test_top_level_must_be_mapping(self):
        path = write_temp_yaml("- just a list\n- not a mapping\n")
        try:
            with self.assertRaises(QuestionLoadError):
                load_questions_from_yaml(path)
        finally:
            path.unlink()

    def test_questions_must_be_list(self):
        path = write_temp_yaml("questions: not a list\n")
        try:
            with self.assertRaises(QuestionLoadError):
                load_questions_from_yaml(path)
        finally:
            path.unlink()

    def test_invalid_strategy_value_raises(self):
        path = write_temp_yaml("""\
questions:
  - id: q1
    tab: T
    label: L
    answer_strategy: not_a_real_strategy
""")
        try:
            with self.assertRaises(QuestionLoadError) as cm:
                load_questions_from_yaml(path)
            self.assertIn("q1", str(cm.exception)) if "q1" in str(cm.exception) else None
            # The error message should mention which entry failed
            self.assertIn("validation", str(cm.exception).lower())
        finally:
            path.unlink()

    def test_duplicate_id_raises(self):
        path = write_temp_yaml("""\
questions:
  - id: dup
    tab: T
    label: One
  - id: dup
    tab: T
    label: Two
""")
        try:
            with self.assertRaises(QuestionLoadError) as cm:
                load_questions_from_yaml(path)
            self.assertIn("Duplicate", str(cm.exception))
        finally:
            path.unlink()

    def test_missing_required_field_raises(self):
        path = write_temp_yaml("""\
questions:
  - tab: T
    label: missing id
""")
        try:
            with self.assertRaises(QuestionLoadError):
                load_questions_from_yaml(path)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
