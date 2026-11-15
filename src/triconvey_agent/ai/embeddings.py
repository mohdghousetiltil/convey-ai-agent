"""Text embeddings through OpenRouter (or OpenAI when a direct key is set).

Both routes use the same model and dimensions, so vectors from either are
comparable. Returns None instead of raising, so callers can fall back to
keyword search when embeddings are unavailable.
"""
from __future__ import annotations

import logging
import os

from triconvey_agent.ai.openai_client import openai_runtime_disabled
from triconvey_agent.app_meta import (
    EMBEDDING_DIMENSIONS,
    OPENAI_EMBEDDING_MODEL,
    OPENROUTER_EMBEDDING_MODEL,
)

LOG = logging.getLogger(__name__)

# Stored next to saved vectors. Vectors from a different model cannot be compared.
EMBEDDING_ID = f"{OPENAI_EMBEDDING_MODEL}@{EMBEDDING_DIMENSIONS}"

# text-embedding-3-large accepts 8,192 tokens per input. Roughly 4 characters
# per token leaves headroom for dense tables and numbers.
MAX_INPUT_CHARS = 24_000
_BATCH_SIZE = 32


def embeddings_available() -> bool:
    has_openai = bool(os.environ.get("OPENAI_API_KEY")) and not openai_runtime_disabled()
    return has_openai or bool(os.environ.get("OPENROUTER_API_KEY"))


def _client_and_model():
    import openai

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key and not openai_runtime_disabled():
        return openai.OpenAI(api_key=api_key), OPENAI_EMBEDDING_MODEL
    return (
        openai.OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
            default_headers={"HTTP-Referer": "https://triconvey.com.au", "X-Title": "TriConvey"},
        ),
        OPENROUTER_EMBEDDING_MODEL,
    )


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed texts in batches. Returns None if any batch fails."""
    if not texts or not embeddings_available():
        return None
    try:
        client, model = _client_and_model()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = [(text or " ")[:MAX_INPUT_CHARS] for text in texts[start : start + _BATCH_SIZE]]
            response = client.embeddings.create(
                model=model,
                input=batch,
                dimensions=EMBEDDING_DIMENSIONS,
                encoding_format="float",
            )
            vectors.extend(item.embedding for item in response.data)
        if len(vectors) != len(texts):
            LOG.warning("Embedding count mismatch: %d texts, %d vectors", len(texts), len(vectors))
            return None
        return vectors
    except Exception as exc:
        LOG.warning("Embedding failed, falling back to keyword search: %s", exc)
        return None


def embed_text(text: str) -> list[float] | None:
    result = embed_texts([text])
    return result[0] if result else None
