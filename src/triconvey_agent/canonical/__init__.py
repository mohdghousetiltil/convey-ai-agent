"""Canonical layer — the new 5-brain architecture.

This package is the home of the layered pipeline:

  Brain A — facts/         Document understanding & evidence layer
  Brain B — questions/     Question router (deterministic / grounded AI / review)
  Brain C — policy/        Client policy overlay (presentation only)
  Brain D — field_mapping/ AnswerObject → form action plan
  Brain E — execution/     Desktop executor (pywinauto + verify + recover)

For step 1 only the schema files exist. The existing pipeline continues to
work unchanged. Modules under canonical/ are introduced one brain at a time.
"""
