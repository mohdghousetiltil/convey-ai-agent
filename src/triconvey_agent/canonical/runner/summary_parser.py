from __future__ import annotations

import re
from pathlib import Path


def parse_value(raw):
    raw = raw.strip()
    if raw in {"---", "—", ""}:
        return None
    if raw.startswith(("'", '"')):
        quote = raw[0]
        end = raw.rfind(quote)
        return raw[1:end] if end > 0 else raw[1:]
    if raw == "True":
        return True
    if raw == "False":
        return False
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def infer_control_type(label, value):
    label_lower = label.lower()
    if "(checkbox)" in label_lower or "(tick if" in label_lower:
        return "CheckBox"
    if isinstance(value, bool):
        return "CheckBox"
    if "(dropdown)" in label_lower or label_lower.endswith("zone") or "planning zone" in label_lower:
        return "ComboBox"
    return "Edit"


def clean_label(label):
    return re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()


def parse_summary(path: str | Path) -> list[dict]:
    instructions = []
    current_tab = None
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip()
            match = re.match(r"^\s*---\s*(.+?)\s*---\s*$", line)
            if match:
                tab_name = match.group(1).strip()
                current_tab = None if tab_name.lower() == "internal" else tab_name
                continue

            if current_tab is None:
                continue

            match = re.match(r"^\s*[*!]\s+(AUTO|REVIEW)\s+(.+?)\s{2,}(.+?)\s*$", line)
            if not match:
                continue

            status = match.group(1)
            label = match.group(2).strip()
            raw_value = match.group(3).strip()

            if status == "REVIEW":
                instructions.append(
                    {
                        "tab": current_tab,
                        "label": label,
                        "clean_label": clean_label(label),
                        "value": None,
                        "status": "REVIEW",
                        "control_type": None,
                        "skip": True,
                        "raw_value": raw_value,
                    }
                )
                continue

            value_text = re.sub(r"\s*\[[^\]]+\]\s*$", "", raw_value).strip()
            value = parse_value(value_text)
            instructions.append(
                {
                    "tab": current_tab,
                    "label": label,
                    "clean_label": clean_label(label),
                    "value": value,
                    "status": status,
                    "control_type": infer_control_type(label, value),
                    "skip": value is None,
                }
            )
    return instructions
