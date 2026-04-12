#!/usr/bin/env python
"""Triconvey Agent — end-to-end pipeline runner."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from triconvey_agent.ai.openai_client import OpenAIResponsesClient  # noqa: E402
from triconvey_agent.canonical.runner import default_docs, run_pipeline  # noqa: E402

_YAML_DIR = Path(__file__).resolve().parent / "triconvey-mapping"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Triconvey Agent pipeline and produce answered JSON."
    )
    parser.add_argument(
        "--docs",
        nargs="+",
        metavar="PDF",
        help="Paths to PDF documents to process (defaults to the local samples bundle).",
    )
    parser.add_argument(
        "--out",
        default="output",
        metavar="DIR",
        help="Output directory for JSON files (default: ./output).",
    )
    parser.add_argument(
        "--yaml-dir",
        default=str(_YAML_DIR),
        metavar="DIR",
        help="Directory containing tab_sec_32_*.yaml files for Brain D field matching.",
    )
    parser.add_argument(
        "--use-ai-review",
        action="store_true",
        help="Enable AI-backed grounded answers plus a strict review report.",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        metavar="MODEL",
        help="OpenAI model for grounded answers / review (default: gpt-4o).",
    )
    args = parser.parse_args()

    doc_paths = [Path(p) for p in args.docs] if args.docs else default_docs()
    if not doc_paths:
        parser.error("No PDF files found in default samples folder: samples/")

    ai_client = None
    if args.use_ai_review:
        try:
            ai_client = OpenAIResponsesClient(model=args.model)
            print(f"[AI] Enabled grounded answers + strict review using model {args.model}")
        except ValueError as exc:
            print(f"[WARN] AI disabled: {exc}")

    run_pipeline(
        doc_paths,
        Path(args.out),
        Path(args.yaml_dir),
        ai_client=ai_client,
        use_ai_review=args.use_ai_review,
    )


if __name__ == "__main__":
    main()
