import json
import argparse
from pathlib import Path
from typing import Any, Dict, List


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return str(value)


def extract_answered_questions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for tab in data.get("tabs", []):
        tab_name = tab.get("tab_name", "")
        section = tab.get("section", "")
        for question in tab.get("questions", []):
            if question.get("answer") is not None:
                rows.append(
                    {
                        "tab_name": tab_name,
                        "section": section,
                        "question_id": question.get("question_id", ""),
                        "label": question.get("label", ""),
                        "answer": question.get("answer"),
                        "ai_source_file": question.get("ai_source_file"),
                        "ai_quote": question.get("ai_quote"),
                    }
                )
    return rows


def write_txt(rows: List[Dict[str, Any]], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as f:
        f.write("Answered Questions Only\n")
        f.write("=" * 80 + "\n\n")

        for i, row in enumerate(rows, start=1):
            f.write(f"{i}. Tab: {row['tab_name']}\n")
            f.write(f"   Section: {row['section']}\n")
            f.write(f"   Question ID: {row['question_id']}\n")
            f.write(f"   Label: {row['label']}\n")
            f.write(f"   Answer: {format_value(row['answer'])}\n")

            if row.get("ai_source_file"):
                f.write(f"   Source File: {row['ai_source_file']}\n")
            if row.get("ai_quote"):
                f.write(f"   Quote: {row['ai_quote']}\n")

            f.write("\n")

        f.write("=" * 80 + "\n")
        f.write(f"Total answered questions: {len(rows)}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a JSON file to a TXT file containing only questions with non-null answers."
    )
    parser.add_argument("input_json", help="Path to the input JSON file")
    parser.add_argument(
        "-o",
        "--output",
        help="Path to the output TXT file (default: same name as input with .txt extension)",
    )
    args = parser.parse_args()

    input_path = Path(args.input_json)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path = Path(args.output) if args.output else input_path.with_suffix(".txt")

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    rows = extract_answered_questions(data)
    write_txt(rows, output_path)

    print(f"Wrote {len(rows)} answered questions to: {output_path}")


if __name__ == "__main__":
    main()
