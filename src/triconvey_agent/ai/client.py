from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class AIResult:
    raw_text: str


class AIClient(Protocol):
    def complete(self, prompt: str) -> AIResult:
        ...
