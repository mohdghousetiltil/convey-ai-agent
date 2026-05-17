from __future__ import annotations

import base64
import mimetypes
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from triconvey_agent.ai.client import AIResult


# Timed backoff: rate-limit errors pause OpenAI for 60 s then retry.
# Quota-exhausted errors pause for 5 minutes.
# Neither disables OpenAI permanently across the process lifetime.
_OPENAI_DISABLED_UNTIL: float = 0.0
_BACKOFF_RATE_LIMIT_SECONDS = 60
_BACKOFF_QUOTA_SECONDS = 300


def openai_runtime_disabled() -> bool:
    return time.monotonic() < _OPENAI_DISABLED_UNTIL


def _mark_openai_rate_limited() -> None:
    global _OPENAI_DISABLED_UNTIL
    _OPENAI_DISABLED_UNTIL = time.monotonic() + _BACKOFF_RATE_LIMIT_SECONDS


def _mark_openai_quota_exhausted() -> None:
    global _OPENAI_DISABLED_UNTIL
    _OPENAI_DISABLED_UNTIL = time.monotonic() + _BACKOFF_QUOTA_SECONDS


def _is_insufficient_quota_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "insufficient_quota" in message
        or "exceeded your current quota" in message
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "rate limit" in message or "429" in message or "ratelimit" in message


class OpenAIResponsesClient:
    def __init__(self, api_key: str | None = None, model: str = "gpt-4o") -> None:
        load_dotenv()
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")

        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "The 'openai' package is not installed. Install it with "
                "\"python -m pip install openai\" and try again."
            ) from exc

        if resolved_api_key:
            self._client = OpenAI(api_key=resolved_api_key)
            self._model = model
        else:
            # Fall back to OpenRouter using the OpenAI-compatible endpoint.
            openrouter_key = os.getenv("OPENROUTER_API_KEY")
            if not openrouter_key:
                raise ValueError("Neither OPENAI_API_KEY nor OPENROUTER_API_KEY is set.")
            self._client = OpenAI(
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://triconvey.com.au",
                    "X-Title": "TriConvey",
                },
            )
            # Swap OpenAI model names to a compatible free OpenRouter model.
            if model.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
                self._model = "nvidia/nemotron-3-super-120b-a12b:free"
            else:
                self._model = model

    def complete(self, prompt: str) -> AIResult:
        if openai_runtime_disabled():
            raise RuntimeError("OpenAI temporarily unavailable (rate-limit or quota backoff). Retrying shortly.")
        try:
            response = self._client.responses.create(
                model=self._model,
                input=prompt,
                temperature=0,
            )
            return AIResult(raw_text=response.output_text)
        except Exception as exc:
            if _is_insufficient_quota_error(exc):
                _mark_openai_quota_exhausted()
                raise
            if _is_rate_limit_error(exc):
                _mark_openai_rate_limited()
                raise
            # Fallback for environments where Responses API shape differs.
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    temperature=0,
                    messages=[
                        {"role": "user", "content": prompt},
                    ],
                )
                text = response.choices[0].message.content or ""
                return AIResult(raw_text=text)
            except Exception as fallback_exc:
                if _is_insufficient_quota_error(fallback_exc):
                    _mark_openai_quota_exhausted()
                elif _is_rate_limit_error(fallback_exc):
                    _mark_openai_rate_limited()
                raise

    def complete_vision(self, prompt: str, image_paths: list[str], *, max_tokens: int = 300) -> AIResult:
        if not image_paths:
            return self.complete(prompt)

        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            path = Path(image_path)
            mime, _ = mimetypes.guess_type(path.name)
            mime = mime or "image/png"
            raw = path.read_bytes()
            data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
            )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                max_tokens=max_tokens,
                messages=[
                    {"role": "user", "content": content},
                ],
            )
            text = response.choices[0].message.content or ""
            return AIResult(raw_text=text)
        except Exception as exc:
            if _is_insufficient_quota_error(exc):
                _mark_openai_quota_exhausted()
            elif _is_rate_limit_error(exc):
                _mark_openai_rate_limited()
            return self.complete(prompt)
