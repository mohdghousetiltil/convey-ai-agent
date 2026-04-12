"""Brain E — Desktop form executor.

Reads a FormActionPlan (from Brain D) and fills the TriConvey desktop
application using pywinauto. Requires pywinauto to be installed and
TriConvey to be running and visible on screen.

Usage:
    from triconvey_agent.canonical.brain_e import execute_action_plan

    report = execute_action_plan(plan, dry_run=False)
"""
from triconvey_agent.canonical.brain_e.executor import execute_action_plan

__all__ = ["execute_action_plan"]
