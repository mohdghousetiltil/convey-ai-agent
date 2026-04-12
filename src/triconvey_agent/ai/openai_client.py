from __future__ import annotations

import os

from dotenv import load_dotenv

from triconvey_agent.ai.client import AIResult


class OpenAIResponsesClient:
    def __init__(self, api_key: str | None = None, model: str = "gpt-4o") -> None:
        load_dotenv()
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_api_key:
            raise ValueError("OPENAI_API_KEY is not set.")

        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "The 'openai' package is not installed. Install it with "
                "\"python -m pip install openai\" and try again."
            ) from exc

        self._client = OpenAI(api_key=resolved_api_key)
        self._model = model

    def complete(self, prompt: str) -> AIResult:
        try:
            response = self._client.responses.create(
                model=self._model,
                input=prompt,
                temperature=0,
            )
            return AIResult(raw_text=response.output_text)
        except Exception:
            # Fallback for environments where Responses API shape differs.
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[
                    {"role": "user", "content": prompt},
                ],
            )
            text = response.choices[0].message.content or ""
            return AIResult(raw_text=text)
