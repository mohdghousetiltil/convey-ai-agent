"""
MultiModelClient — unified wrapper for OpenAI, Anthropic, and Google Gemini.

Provides:
  - chat(messages, system, tools, max_tokens) → MultiModelResponse
  - embed(text) → list[float]   (1536-dim; falls back to sentence-transformers + padding)

Best default models:
  OpenAI    → gpt-4o
  Anthropic → claude-sonnet-4-5
  Google    → gemini-1.5-pro
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid_mod
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger(__name__)

_EMBED_DIM = 1536  # canonical embedding dimension


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class MultiModelResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"   # "end_turn" | "tool_use"
    raw: Any = None


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


_OPENROUTER_EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
_OPENROUTER_CHAT_DEFAULT = "nvidia/nemotron-3-super-120b-a12b:free"


class MultiModelClient:
    """Unified AI client for OpenAI, Anthropic, Google Gemini, and OpenRouter."""

    BEST_MODELS: dict[str, str] = {
        "openai": "gpt-4o",
        "anthropic": "claude-sonnet-4-5",
        "google": "gemini-1.5-pro",
        "openrouter": _OPENROUTER_CHAT_DEFAULT,
    }

    def __init__(
        self,
        provider: str = "openrouter",
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        resolved_provider = self._detect_default_provider() if provider == "openrouter" and not model else provider.lower()
        self.model = model or self.BEST_MODELS.get(resolved_provider, _OPENROUTER_CHAT_DEFAULT)
        # Auto-detect provider from model name so Claude models always use Anthropic etc.
        self.provider = self._detect_provider(self.model, resolved_provider)
        self._api_key = api_key
        self._client = self._build_client()

    @staticmethod
    def _detect_default_provider() -> str:
        """Return the best available provider based on which API keys are set."""
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        if os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY"):
            return "anthropic"
        if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
            return "google"
        # Default: OpenRouter (works with OPENROUTER_API_KEY or even without for :free models)
        return "openrouter"

    @staticmethod
    def _detect_provider(model: str, preferred: str) -> str:
        m = model.lower()
        if m.startswith("claude-"):
            return "anthropic"
        if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt", "text-embedding")):
            return "openai"
        if m.startswith("gemini"):
            return "google"
        if "/" in m or preferred == "openrouter":
            return "openrouter"
        return preferred

    # ------------------------------------------------------------------ #
    # Public interface                                                      #
    # ------------------------------------------------------------------ #

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> MultiModelResponse:
        """Send a chat request and return a normalised MultiModelResponse."""
        if self.provider == "anthropic":
            return self._chat_anthropic(
                messages, system=system, tools=tools, max_tokens=max_tokens
            )
        elif self.provider == "google":
            return self._chat_google(
                messages, system=system, tools=tools, max_tokens=max_tokens
            )
        else:
            # Both "openai" and "openrouter" use the OpenAI-compatible client.
            return self._chat_openai(
                messages, system=system, tools=tools, max_tokens=max_tokens
            )

    def embed(self, text: str) -> list[float]:
        """
        Generate an embedding vector (always _EMBED_DIM = 1536 dims).

        Priority:
          1. OpenAI text-embedding-3-small  (if OPENAI_API_KEY set)
          2. OpenRouter nvidia/llama-nemotron-embed-vl-1b-v2:free  (if OPENROUTER_API_KEY set)
          3. sentence-transformers all-MiniLM-L6-v2 (384-dim, zero-padded to 1536)
        """
        openai_key = os.getenv("OPENAI_API_KEY") or ""
        if openai_key:
            try:
                return self._embed_openai(text, api_key=openai_key)
            except Exception as exc:
                LOG.warning("OpenAI embed failed, trying OpenRouter: %s", exc)

        openrouter_key = os.getenv("OPENROUTER_API_KEY") or ""
        if openrouter_key:
            try:
                return self._embed_openrouter(text, api_key=openrouter_key)
            except Exception as exc:
                LOG.warning("OpenRouter embed failed, falling back to local: %s", exc)

        return self._embed_local(text)

    # ------------------------------------------------------------------ #
    # Client builders                                                       #
    # ------------------------------------------------------------------ #

    def _build_client(self) -> Any:
        if self.provider == "anthropic":
            return self._build_anthropic()
        elif self.provider == "google":
            return self._build_google()
        elif self.provider == "openrouter":
            return self._build_openrouter()
        else:
            return self._build_openai()

    def _build_openai(self) -> Any:
        from openai import OpenAI
        key = self._api_key or os.getenv("OPENAI_API_KEY")
        if key:
            return OpenAI(api_key=key)
        # Fallback: use OpenRouter with the OpenAI-compatible API
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if not openrouter_key:
            raise ValueError("Neither OPENAI_API_KEY nor OPENROUTER_API_KEY is set. Add one in Settings.")
        return OpenAI(
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://triconvey.com.au",
                "X-Title": "TriConvey",
            },
        )

    def _build_openrouter(self) -> Any:
        from openai import OpenAI
        key = self._api_key or os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("OPENROUTER_API_KEY is not set. Add it in Settings.")
        return OpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://triconvey.com.au",
                "X-Title": "TriConvey Matter Agent",
            },
        )

    def _build_anthropic(self) -> Any:
        import anthropic
        key = self._api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is not set. Add it in Settings.")
        return anthropic.Anthropic(api_key=key)

    def _build_google(self) -> Any:
        try:
            import google.generativeai as genai  # noqa: F401
        except ImportError:
            raise ImportError(
                "google-generativeai is not installed. "
                "Run: pip install google-generativeai"
            )
        key = self._api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError(
                "GOOGLE_API_KEY is not set. Add it in Settings → AI Provider → Google."
            )
        import google.generativeai as genai
        genai.configure(api_key=key)
        return genai

    # ------------------------------------------------------------------ #
    # OpenAI chat                                                           #
    # ------------------------------------------------------------------ #

    def _chat_openai(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> MultiModelResponse:
        import json
        full_messages: list[dict[str, Any]] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": full_messages,
        }
        if tools:
            kwargs["tools"] = [self._to_openai_tool(t) for t in tools]
            kwargs["tool_choice"] = "auto"

        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        stop_reason = "tool_use" if tool_calls else "end_turn"
        return MultiModelResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw=resp,
        )

    # ------------------------------------------------------------------ #
    # Anthropic chat                                                        #
    # ------------------------------------------------------------------ #

    def _chat_anthropic(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> MultiModelResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools  # Anthropic native format

        resp = self._client.messages.create(**kwargs)
        content_text = "\n".join(
            b.text for b in resp.content if hasattr(b, "text")
        ).strip()

        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input or {},
                ))

        stop_reason = "tool_use" if resp.stop_reason == "tool_use" else "end_turn"
        return MultiModelResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw=resp,
        )

    # ------------------------------------------------------------------ #
    # Google Gemini chat                                                    #
    # ------------------------------------------------------------------ #

    def _chat_google(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> MultiModelResponse:
        import google.generativeai as genai

        tool_declarations = self._to_google_tools(tools) if tools else None

        model_kwargs: dict[str, Any] = {"model_name": self.model}
        if system:
            model_kwargs["system_instruction"] = system
        if tool_declarations:
            model_kwargs["tools"] = tool_declarations

        model_obj = genai.GenerativeModel(**model_kwargs)
        generation_config = genai.types.GenerationConfig(max_output_tokens=max_tokens)

        # Convert to Google history format
        google_history: list[dict[str, Any]] = []
        last_user_msg = "Please continue."
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "user":
                google_history.append({"role": "user", "parts": [str(content)]})
            elif role == "assistant":
                google_history.append({"role": "model", "parts": [str(content)]})

        if google_history and google_history[-1]["role"] == "user":
            last_user_msg = google_history.pop()["parts"][0]

        chat = model_obj.start_chat(history=google_history)
        response = chat.send_message(
            last_user_msg, generation_config=generation_config
        )

        content_text = ""
        tool_calls: list[ToolCall] = []
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    content_text += part.text
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    args: dict[str, Any] = {}
                    try:
                        args = dict(fc.args)
                    except Exception:
                        pass
                    tool_calls.append(ToolCall(
                        id=str(_uuid_mod.uuid4()),
                        name=fc.name,
                        arguments=args,
                    ))

        stop_reason = "tool_use" if tool_calls else "end_turn"
        return MultiModelResponse(
            content=content_text.strip(),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw=response,
        )

    # ------------------------------------------------------------------ #
    # Embedding                                                             #
    # ------------------------------------------------------------------ #

    def _embed_openai(self, text: str, api_key: str) -> list[float]:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.embeddings.create(
            model="text-embedding-3-small",
            input=text[:8191],
        )
        return resp.data[0].embedding  # 1536 dims natively

    def _embed_openrouter(self, text: str, api_key: str) -> list[float]:
        """Embed via OpenRouter using the free Nemotron embed model.

        OpenRouter's /embeddings endpoint is OpenAI-compatible.
        The model may return dimensions != 1536, so we pad/truncate to _EMBED_DIM.
        """
        import httpx
        resp = httpx.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://triconvey.com.au",
                "X-Title": "TriConvey Matter Agent",
                "Content-Type": "application/json",
            },
            json={"model": _OPENROUTER_EMBED_MODEL, "input": text[:8191]},
            timeout=30,
        )
        resp.raise_for_status()
        vec: list[float] = resp.json()["data"][0]["embedding"]
        # Normalise to the canonical _EMBED_DIM regardless of model output size.
        if len(vec) < _EMBED_DIM:
            vec = vec + [0.0] * (_EMBED_DIM - len(vec))
        return vec[:_EMBED_DIM]

    def _embed_local(self, text: str) -> list[float]:
        """Sentence-transformers fallback, zero-padded to _EMBED_DIM."""
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            vec: list[float] = model.encode(text, normalize_embeddings=True).tolist()
            if len(vec) < _EMBED_DIM:
                vec = vec + [0.0] * (_EMBED_DIM - len(vec))
            return vec[:_EMBED_DIM]
        except Exception as exc:
            LOG.warning("Local embedding failed (%s) — returning zero vector", exc)
            return [0.0] * _EMBED_DIM

    # ------------------------------------------------------------------ #
    # Tool format converters                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool.get("input_schema", {}),
            },
        }

    @staticmethod
    def _to_google_tools(tools: list[dict[str, Any]]) -> list[Any]:
        """Convert Anthropic-style tool defs → Google function declarations."""
        try:
            import google.generativeai as genai
        except ImportError:
            return []

        def _schema_from_jsonschema(node: dict[str, Any]) -> Any:
            json_type = (node.get("type") or "string").lower()
            description = node.get("description", "")
            type_map: dict[str, str] = {
                "string": "STRING",
                "number": "NUMBER",
                "integer": "INTEGER",
                "boolean": "BOOLEAN",
                "array": "ARRAY",
                "object": "OBJECT",
            }
            g_type = type_map.get(json_type, "STRING")

            if json_type == "array":
                items_node = node.get("items") or {"type": "string"}
                return genai.protos.Schema(
                    type=genai.protos.Type[g_type],
                    description=description,
                    items=_schema_from_jsonschema(items_node),
                )

            if json_type == "object":
                properties: dict[str, Any] = {}
                for prop_name, prop_def in (node.get("properties") or {}).items():
                    try:
                        properties[prop_name] = _schema_from_jsonschema(prop_def or {})
                    except Exception:
                        properties[prop_name] = genai.protos.Schema(type=genai.protos.Type.STRING)
                return genai.protos.Schema(
                    type=genai.protos.Type[g_type],
                    description=description,
                    properties=properties,
                    required=node.get("required", []),
                )

            try:
                return genai.protos.Schema(
                    type=genai.protos.Type[g_type],
                    description=description,
                )
            except Exception:
                return genai.protos.Schema(
                    type=genai.protos.Type.STRING,
                    description=description,
                )

        declarations = []

        for tool in tools:
            schema = tool.get("input_schema", {})
            properties: dict[str, Any] = {}
            for prop_name, prop_def in (schema.get("properties") or {}).items():
                try:
                    properties[prop_name] = _schema_from_jsonschema(prop_def or {})
                except Exception:
                    properties[prop_name] = genai.protos.Schema(type=genai.protos.Type.STRING)

            try:
                fn_decl = genai.protos.FunctionDeclaration(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=genai.protos.Schema(
                        type=genai.protos.Type.OBJECT,
                        properties=properties,
                        required=schema.get("required", []),
                    ),
                )
                declarations.append(fn_decl)
            except Exception as exc:
                LOG.warning("Could not build Google function declaration for %s: %s", tool.get("name"), exc)

        if not declarations:
            return []
        try:
            return [genai.protos.Tool(function_declarations=declarations)]
        except Exception as exc:
            LOG.warning("Could not build Google Tool wrapper: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_client(provider: str, model: str | None = None, api_key: str | None = None) -> MultiModelClient:
    """Create a MultiModelClient for the given provider."""
    return MultiModelClient(provider=provider, model=model, api_key=api_key)
