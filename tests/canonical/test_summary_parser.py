from __future__ import annotations

from pathlib import Path

from triconvey_agent.canonical.runner.summary_parser import parse_summary


def test_parse_summary_extracts_tabs_and_skip_flags(tmp_path: Path):
    sample = """TRICONVEY AGENT - ANSWER SUMMARY
============================================================

--- Sec. 32 (1) ---
  * AUTO       Their total does not exceed (checkbox)                True
  ! REVIEW     1.1 Outgoing 4 - Authority name                       ---  [needs review]

--- Internal ---
  * AUTO       Ignored                                               'x'
"""
    summary_path = tmp_path / "summary.txt"
    summary_path.write_text(sample, encoding="utf-8")

    instructions = parse_summary(summary_path)

    assert len(instructions) == 2
    assert instructions[0]["tab"] == "Sec. 32 (1)"
    assert instructions[0]["control_type"] == "CheckBox"
    assert instructions[0]["value"] is True
    assert instructions[0]["skip"] is False
    assert instructions[1]["status"] == "REVIEW"
    assert instructions[1]["skip"] is True
