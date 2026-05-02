from triconvey_agent.copy_rules import find_best_copy_rule_match, normalize_authority_name


def test_normalize_authority_name_handles_common_suffixes_and_typos() -> None:
    assert normalize_authority_name("Yarra Vally Water Authority") == "yarra valley water"
    assert normalize_authority_name("Central Highlands Water") == "central highlands water"


def test_find_best_copy_rule_match_prefers_expected_water_authority() -> None:
    rules = [
        ("Yarra Valley Water", 774.72),
        ("Central Highlands Water", 1035.40),
        ("Greater Western Water", 737.01),
    ]

    match = find_best_copy_rule_match("Yarra Vally Water Authority", rules)

    assert match is not None
    assert match.authority_name == "Yarra Valley Water"
    assert match.annual_amount == 774.72
    assert match.score >= 0.9
