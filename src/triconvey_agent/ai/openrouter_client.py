"""OpenRouter AI client — wraps the OpenRouter chat-completions API.

Default model: nvidia/nemotron-nano-4b-instruct (text) or the multimodal
variant configured via OPENROUTER_MODEL.  Nemotron Omni supports text,
image, audio and video and is positioned for document-intelligence workflows.

Environment variables
---------------------
OPENROUTER_API_KEY   — required
OPENROUTER_MODEL     — optional, defaults to nvidia/llama-3.1-nemotron-nano-8b-instruct
OPENROUTER_TIMEOUT   — optional, seconds (default 45)
"""
from __future__ import annotations

import os
import time

import requests

from triconvey_agent.ai.client import AIResult

_DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
_FALLBACK_MODELS = [
    "inclusionai/ring-2.6-1t:free",
    "openai/gpt-4o-mini",
]
_FALLBACK_DELAY_SECONDS = 2
_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_SYSTEM_PROMPT = (
    "You are a document table perception agent for Australian conveyancing documents. "
    "Return only valid JSON in assistant message.content. "
    "Do not output reasoning-only responses. "
    "Do not invent values. Do not hallucinate amounts. "
    "If unsure, return a minimal valid JSON object with empty rows."
)


class OpenRouterClient:
    """AIClient-compatible wrapper around the OpenRouter completions endpoint.

    Satisfies the AIClient Protocol so it can be passed directly to any
    extractor that accepts an ai_client parameter.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY environment variable is not set. "
                "Set it before constructing OpenRouterClient."
            )
        self.model = model or os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        self.timeout = timeout or int(os.getenv("OPENROUTER_TIMEOUT", "45"))

    # ------------------------------------------------------------------
    # Core method — satisfies AIClient Protocol
    # ------------------------------------------------------------------

    def complete(self, prompt: str, *, image_data_url: str | None = None) -> AIResult:
        """Send a completion request to OpenRouter with automatic fallback.

        Tries the configured model first, then each fallback in order with a
        ``_FALLBACK_DELAY_SECONDS`` pause between attempts.

        Parameters
        ----------
        prompt:
            The user-facing text prompt.
        image_data_url:
            Optional base64-encoded data URL ("data:image/png;base64,…")
            for multimodal models.  Ignored for text-only models.
        """
        models_to_try = [self.model] + _FALLBACK_MODELS
        last_exc: Exception | None = None

        for attempt, model in enumerate(models_to_try):
            if attempt > 0:
                time.sleep(_FALLBACK_DELAY_SECONDS)
            try:
                return self._complete_with_model(prompt, model=model, image_data_url=image_data_url)
            except Exception as exc:
                last_exc = exc
                continue

        raise RuntimeError(
            f"All OpenRouter models failed. Last error: {last_exc}"
        ) from last_exc

    def _complete_with_model(
        self,
        prompt: str,
        *,
        model: str,
        image_data_url: str | None = None,
    ) -> AIResult:
        content: list[dict] = [{"type": "text", "text": prompt}]
        if image_data_url:
            content.append(
                {"type": "image_url", "image_url": {"url": image_data_url}}
            )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://triconvey.com.au",
            "X-Title": "TriConvey Rates Extraction",
        }

        response = requests.post(
            _API_URL,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()

        data = response.json()
        try:
            msg = data["choices"][0]["message"]
        except Exception as exc:
            raise RuntimeError(
                f"OpenRouter response missing choices/message: {data}"
            ) from exc

        body = msg.get("content")
        if isinstance(body, list):
            text_parts: list[str] = []
            for part in body:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(str(part.get("text") or ""))
                    elif "text" in part:
                        text_parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    text_parts.append(part)
            body = "\n".join(x for x in text_parts if x.strip())

        if body is None:
            reasoning = msg.get("reasoning")
            raise RuntimeError(
                "OpenRouter returned null content. "
                "Model produced reasoning but no final JSON in message.content. "
                f"model={data.get('model')}; reasoning_preview={str(reasoning)[:300]!r}"
            )

        raw = str(body).strip()
        if not raw:
            raise RuntimeError(f"OpenRouter returned empty content: {data}")

        return AIResult(raw_text=raw)
