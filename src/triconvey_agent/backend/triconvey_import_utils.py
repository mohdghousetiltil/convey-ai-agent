from __future__ import annotations

from typing import Any, Callable

from triconvey_agent.copy_rules import find_best_copy_rule_match


def best_fact_by_file(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for fact in facts:
        sources = fact.get("sources") or []
        source = sources[0] if sources and isinstance(sources[0], dict) else {}
        file_name = str(source.get("file") or "").strip()
        if not file_name:
            continue
        current = grouped.get(file_name)
        if current is None or float(fact.get("confidence") or 0.0) >= float(current.get("confidence") or 0.0):
            grouped[file_name] = fact
    return grouped


def parse_amount_text(value: Any) -> float | None:
    if value in (None, "", [], {}):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def set_answer_value(answer: dict[str, Any], value: str | None) -> dict[str, Any]:
    next_answer = dict(answer or {})
    next_answer["value_json"] = value
    next_answer["human_value_json"] = None
    next_answer["needs_review"] = False
    next_answer["review_reasons"] = []
    return next_answer


def collect_water_rows_from_facts(
    facts_by_path: dict[str, Any],
    rules: list[Any],
) -> list[dict[str, str]]:
    authority_facts = [fact for fact in list(facts_by_path.get("rates.water.authority_name") or []) if isinstance(fact, dict)]
    amount_facts = [fact for fact in list(facts_by_path.get("rates.water.annual_amount") or []) if isinstance(fact, dict)]

    authority_by_file = best_fact_by_file(authority_facts)
    amount_by_file = best_fact_by_file(amount_facts)

    rows: list[dict[str, str]] = []
    for file_name in sorted(set(authority_by_file) | set(amount_by_file)):
        authority_fact = authority_by_file.get(file_name)
        amount_fact = amount_by_file.get(file_name)
        authority_name = str((authority_fact or {}).get("value") or "").strip()
        amount_value = str((amount_fact or {}).get("value") or "").strip()
        if not authority_name:
            continue
        if parse_amount_text(amount_value) is None:
            match = find_best_copy_rule_match(
                authority_name,
                [(row.authority_name, row.annual_amount) for row in rules],
            )
            if match is not None:
                amount_value = f"${match.annual_amount:,.2f}"
        if parse_amount_text(amount_value) is None:
            continue
        rows.append({"authority": authority_name, "amount": amount_value})
    return rows


def apply_multi_water_outgoing_rows(
    answers: dict[str, Any],
    facts_by_path: dict[str, Any],
    rules: list[Any],
) -> None:
    water_rows = collect_water_rows_from_facts(facts_by_path, rules)
    if len(water_rows) <= 1:
        return

    existing_non_water: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    water_keys = {(row["authority"].strip().lower(), row["amount"].strip()) for row in water_rows}
    for row_num in range(2, 5):
        authority_answer = answers.get(f"sec32_1.1_outgoing_{row_num}_authority") or {}
        amount_answer = answers.get(f"sec32_1.1_outgoing_{row_num}_amount") or {}
        authority_value = str(authority_answer.get("human_value_json") or authority_answer.get("value_json") or "").strip()
        amount_value = str(amount_answer.get("human_value_json") or amount_answer.get("value_json") or "").strip()
        if not authority_value or parse_amount_text(amount_value) is None:
            continue
        key = (authority_value.lower(), amount_value)
        if key in water_keys or key in seen_pairs:
            continue
        seen_pairs.add(key)
        existing_non_water.append({"authority": authority_value, "amount": amount_value})

    combined_rows = (water_rows + existing_non_water)[:3]
    for index, row_num in enumerate(range(2, 5)):
        authority_id = f"sec32_1.1_outgoing_{row_num}_authority"
        amount_id = f"sec32_1.1_outgoing_{row_num}_amount"
        if index < len(combined_rows):
            row = combined_rows[index]
            answers[authority_id] = set_answer_value(answers.get(authority_id) or {}, row["authority"])
            answers[amount_id] = set_answer_value(answers.get(amount_id) or {}, row["amount"])
        else:
            answers[authority_id] = set_answer_value(answers.get(authority_id) or {}, None)
            answers[amount_id] = set_answer_value(answers.get(amount_id) or {}, None)


def wait_for_triconvey_paths(
    explicit_paths: list[str],
    *,
    sleeper: Callable[[float], None],
    delay_seconds: float = 2.0,
) -> None:
    if explicit_paths:
        sleeper(delay_seconds)
