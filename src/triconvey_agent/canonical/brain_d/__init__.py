"""Brain D — Field Mapper.

Takes AnswerObjects (Brain B output) and produces a FormActionPlan
that Brain E (pywinauto) can execute against the TriConvey desktop form.

Public API
----------
build_action_plan(answers, yaml_dir) -> FormActionPlan
"""
from triconvey_agent.canonical.brain_d.mapper import build_action_plan

__all__ = ["build_action_plan"]
