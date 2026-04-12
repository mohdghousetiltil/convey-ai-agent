from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from triconvey_agent.schemas.extracted import FinalExtraction


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: str | Path, payload: Any) -> Path:
    output_path = Path(path).expanduser().resolve()
    ensure_parent_dir(output_path)
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output_path


def write_final_extraction(path: str | Path, extraction: FinalExtraction) -> Path:
    return write_json(path, extraction.model_dump(mode="json"))
