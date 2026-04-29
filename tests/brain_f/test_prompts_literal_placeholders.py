from __future__ import annotations


def test_build_system_prompt_allows_literal_authority_placeholders() -> None:
    from triconvey_agent.brain_f.prompts import build_system_prompt

    prompt = build_system_prompt(mode="standard")
    assert "{Full Council Name}" in prompt
    assert "{Full Water Authority Name}" in prompt
    assert "{OC Name if known}" in prompt

