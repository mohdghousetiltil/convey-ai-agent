from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from triconvey_agent.ai.openai_client import OpenAIResponsesClient

LOG = logging.getLogger(__name__)


@dataclass
class VisionAdvice:
    screenshot: str
    screen_type: str = "unknown"
    next_step: str = "observe"
    confidence: float = 0.0
    summary: str = ""
    raw_text: str = ""
    context: dict[str, Any] | None = None


class VisionAdvisor:
    """Background screenshot analyzer used by Brain E as a fast recovery layer."""

    def __init__(self, *, model: str = "gpt-4.1-nano") -> None:
        try:
            self._client = OpenAIResponsesClient(model=model)
        except Exception as exc:
            LOG.info("Vision advisor disabled: %s", exc)
            self._client = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-advisor")
        self._lock = threading.Lock()
        self._last: VisionAdvice | None = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def submit(self, screenshot: str | None, **context: Any) -> None:
        if not self.enabled or not screenshot or not Path(screenshot).exists():
            return
        self._pool.submit(self._analyze_and_store, screenshot, context)

    def analyze_now(self, screenshot: str | None, **context: Any) -> VisionAdvice | None:
        if not self.enabled or not screenshot or not Path(screenshot).exists():
            return None
        advice = self._analyze(screenshot, context)
        with self._lock:
            self._last = advice
        return advice

    def last(self) -> VisionAdvice | None:
        with self._lock:
            return self._last

    def _analyze_and_store(self, screenshot: str, context: dict[str, Any]) -> None:
        try:
            advice = self._analyze(screenshot, context)
            with self._lock:
                self._last = advice
        except Exception as exc:
            LOG.debug("Vision advisor failed for %s: %s", screenshot, exc)

    def _analyze(self, screenshot: str, context: dict[str, Any]) -> VisionAdvice:
        prompt = self._build_prompt(context)
        result = self._client.complete_vision(prompt, [screenshot], max_tokens=120)  # type: ignore[union-attr]
        data = self._parse(result.raw_text)
        return VisionAdvice(
            screenshot=screenshot,
            screen_type=str(data.get("screen_type") or "unknown"),
            next_step=str(data.get("next_step") or "observe"),
            confidence=float(data.get("confidence") or 0.0),
            summary=str(data.get("summary") or result.raw_text.strip()[:240]),
            raw_text=result.raw_text,
            context=context or None,
        )

    def _build_prompt(self, context: dict[str, Any]) -> str:
        payload = {
            "task": "You are a fast TriConvey vision copilot.",
            "goal": "Identify the current TriConvey screen and the next best action.",
            "context": {
                "stage": context.get("stage"),
                "expected_step": context.get("expected_step"),
                "tab": context.get("tab"),
                "question_id": context.get("question_id"),
                "field_id": context.get("field_id"),
                "search_text": context.get("search_text"),
                "note": context.get("note"),
            },
            "instructions": [
                "Be extremely concise.",
                "Return JSON only.",
                "Use screen_type values like main, matter_search, matter_details, property_details, sec32_tab, unknown.",
                "Use next_step values like search_client, open_matter, open_property_details, switch_tab, fill_field, scroll_down, scroll_up, click_row, confirm, recover, observe.",
                "Prefer the single most useful next step. Do not explain alternatives.",
                "If uncertain, choose recover and explain what is visible.",
            ],
            "schema": {
                "screen_type": "string",
                "next_step": "string",
                "confidence": "number 0..1",
                "summary": "string",
            },
        }
        return json.dumps(payload, ensure_ascii=True)

    def _parse(self, text: str) -> dict[str, Any]:
        raw = text.strip()
        try:
            return json.loads(raw)
        except Exception:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except Exception:
                    pass
        return {}
