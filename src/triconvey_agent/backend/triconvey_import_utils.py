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
        cleaned = str(value).replace("$", "").replace(",", "").strip()
        if cleaned.lower() in {"n/a", "na", "-", "–", "—"}:
            return None
        return float(cleaned)
    except ValueError:
        return None


def set_answer_value(answer: dict[str, Any], value: str | None) -> dict[str, Any]:
    next_answer = dict(answer or {})
    next_answer["value_json"] = value
    next_answer["human_value_json"] = None
    next_answer["needs_review"] = False
    next_answer["review_reasons"] = []
    return next_answer


def _best_fact_value(facts: list[dict[str, Any]]) -> str:
    """Return the value from the highest-confidence fact in the list."""
    if not facts:
        return ""
    best = max(facts, key=lambda f: float(f.get("confidence") or 0.0))
    return str(best.get("value") or "").strip()


def _format_amount(parsed: float | None) -> str:
    if parsed is None:
        return "$0.00"
    return f"${parsed:,.2f}"


def collect_council_row_from_facts(
    facts_by_path: dict[str, Any],
) -> list[dict[str, str]]:
    authority_facts = [f for f in (facts_by_path.get("rates.council.authority_name") or []) if isinstance(f, dict)]
    amount_facts = [f for f in (facts_by_path.get("rates.council.annual_amount") or []) if isinstance(f, dict)]
    if not authority_facts and not amount_facts:
        return []
    authority = _best_fact_value(authority_facts) or "Council"
    parsed = parse_amount_text(_best_fact_value(amount_facts))
    return [{"authority": authority, "amount": _format_amount(parsed)}]


def collect_water_rows_from_facts(
    facts_by_path: dict[str, Any],
    rules: list[Any],
) -> list[dict[str, str]]:
    authority_facts = [f for f in (facts_by_path.get("rates.water.authority_name") or []) if isinstance(f, dict)]
    amount_facts = [f for f in (facts_by_path.get("rates.water.annual_amount") or []) if isinstance(f, dict)]

    authority_by_file = best_fact_by_file(authority_facts)
    amount_by_file = best_fact_by_file(amount_facts)

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for file_name in sorted(set(authority_by_file) | set(amount_by_file)):
        authority_fact = authority_by_file.get(file_name)
        amount_fact = amount_by_file.get(file_name)
        authority_name = str((authority_fact or {}).get("value") or "").strip()
        amount_value = str((amount_fact or {}).get("value") or "").strip()
        if not authority_name:
            continue
        parsed = parse_amount_text(amount_value)
        if parsed is None:
            match = find_best_copy_rule_match(
                authority_name,
                [(row.authority_name, row.annual_amount) for row in rules],
            )
            if match is not None:
                parsed = match.annual_amount
        amount_str = _format_amount(parsed)
        key = (authority_name.lower(), amount_str)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"authority": authority_name, "amount": amount_str})
    return rows


def collect_land_tax_row_from_facts(
    facts_by_path: dict[str, Any],
) -> list[dict[str, str]]:
    authority_facts = [f for f in (facts_by_path.get("rates.land_tax.authority_name") or []) if isinstance(f, dict)]
    amount_facts = [f for f in (facts_by_path.get("rates.land_tax.amount") or []) if isinstance(f, dict)]
    if not authority_facts and not amount_facts:
        return []
    authority = _best_fact_value(authority_facts) or "State Revenue Office"
    parsed = parse_amount_text(_best_fact_value(amount_facts))
    return [{"authority": authority, "amount": _format_amount(parsed)}]


def collect_oc_rows_from_facts(
    facts_by_path: dict[str, Any],
) -> list[dict[str, str]]:
    authority_facts = [f for f in (facts_by_path.get("rates.owners_corporation.authority_name") or []) if isinstance(f, dict)]
    amount_facts = [f for f in (facts_by_path.get("rates.owners_corporation.annual_amount") or []) if isinstance(f, dict)]
    if not authority_facts and not amount_facts:
        return []
    authority = _best_fact_value(authority_facts) or "Owners Corporation"
    parsed = parse_amount_text(_best_fact_value(amount_facts))
    return [{"authority": authority, "amount": _format_amount(parsed)}]


def build_priority_outgoing_rows(
    answers: dict[str, Any],
    facts_by_path: dict[str, Any],
    rules: list[Any],
) -> None:
    """Build priority-ordered authority table and write exactly 4 rows to answers.

    Priority order: council → water (multiple allowed) → land tax → owners corporation.
    Zero amounts are shown as $0.00 rather than suppressed.
    Rows with no supporting documents are omitted; remaining slots are blanked.
    """
    rows: list[dict[str, str]] = []
    rows += collect_council_row_from_facts(facts_by_path)
    rows += collect_water_rows_from_facts(facts_by_path, rules)
    rows += collect_land_tax_row_from_facts(facts_by_path)
    rows += collect_oc_rows_from_facts(facts_by_path)

    rows = rows[:4]

    for i in range(1, 5):
        authority_id = f"sec32_1.1_outgoing_{i}_authority"
        amount_id = f"sec32_1.1_outgoing_{i}_amount"
        idx = i - 1
        if idx < len(rows):
            row = rows[idx]
            answers[authority_id] = set_answer_value(answers.get(authority_id) or {}, row["authority"])
            answers[amount_id] = set_answer_value(answers.get(amount_id) or {}, row["amount"])
        else:
            answers[authority_id] = set_answer_value(answers.get(authority_id) or {}, None)
            answers[amount_id] = set_answer_value(answers.get(amount_id) or {}, None)


def apply_multi_water_outgoing_rows(
    answers: dict[str, Any],
    facts_by_path: dict[str, Any],
    rules: list[Any],
) -> None:
    """Backward-compatible alias for build_priority_outgoing_rows."""
    build_priority_outgoing_rows(answers, facts_by_path, rules)


def wait_for_triconvey_paths(
    explicit_paths: list[str],
    *,
    sleeper: Callable[[float], None],
    delay_seconds: float = 2.0,
) -> None:
    if explicit_paths:
        sleeper(delay_seconds)
