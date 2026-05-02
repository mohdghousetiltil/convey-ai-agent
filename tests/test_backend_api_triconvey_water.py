from triconvey_agent.backend import triconvey_import_utils as utils


class _Rule:
    def __init__(self, authority_name: str, annual_amount: float) -> None:
        self.authority_name = authority_name
        self.annual_amount = annual_amount


def _fact(value: str, file_name: str, confidence: float = 0.97) -> dict:
    return {
        "value": value,
        "confidence": confidence,
        "sources": [{"file": file_name}],
        "extractor": "rule:water_authority_certificate_v1",
    }


def test_apply_multi_water_outgoing_rows_keeps_documents_separate() -> None:
    answers = {
        "sec32_1.1_outgoing_2_authority": {"value_json": "South East Water"},
        "sec32_1.1_outgoing_2_amount": {"value_json": "$450.00"},
        "sec32_1.1_outgoing_3_authority": {"value_json": "State Revenue Office, Land Tax - Annually"},
        "sec32_1.1_outgoing_3_amount": {"value_json": "$700.00"},
        "sec32_1.1_outgoing_4_authority": {"value_json": "Owners Corporation"},
        "sec32_1.1_outgoing_4_amount": {"value_json": "$900.00"},
    }
    facts_by_path = {
        "rates.water.authority_name": [
            _fact("South East Water", "south-east.pdf"),
            _fact("Gippsland Water", "gippsland.pdf"),
        ],
        "rates.water.annual_amount": [
            _fact("$450.00", "south-east.pdf"),
            _fact("$550.00", "gippsland.pdf"),
        ],
    }

    utils.apply_multi_water_outgoing_rows(answers, facts_by_path, rules=[])

    assert answers["sec32_1.1_outgoing_2_authority"]["value_json"] == "Gippsland Water"
    assert answers["sec32_1.1_outgoing_2_amount"]["value_json"] == "$550.00"
    assert answers["sec32_1.1_outgoing_3_authority"]["value_json"] == "South East Water"
    assert answers["sec32_1.1_outgoing_3_amount"]["value_json"] == "$450.00"
    assert answers["sec32_1.1_outgoing_4_authority"]["value_json"] == "State Revenue Office, Land Tax - Annually"
    assert answers["sec32_1.1_outgoing_4_amount"]["value_json"] == "$700.00"


def test_collect_water_rows_uses_copy_rule_for_missing_amount() -> None:
    facts_by_path = {
        "rates.water.authority_name": [_fact("South East Water", "south-east.pdf")],
        "rates.water.annual_amount": [],
    }
    rows = utils.collect_water_rows_from_facts(
        facts_by_path,
        rules=[_Rule("South East Water", 611.25)],
    )

    assert rows == [{"authority": "South East Water", "amount": "$611.25"}]


def test_wait_for_triconvey_paths_delays_resolution() -> None:
    sleeps: list[float] = []

    utils.wait_for_triconvey_paths(["C:/temp/sample.pdf"], sleeper=lambda seconds: sleeps.append(seconds))
    assert sleeps == [2.0]
