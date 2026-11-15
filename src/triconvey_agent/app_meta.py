from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import os
import sys

APP_NAME = "Convey Agent"
APP_SLUG = "TriConveyAgent"
APP_EXECUTABLE_NAME = "ConveyAgent.exe"
APP_PUBLISHER = "Convey Agent"
APP_COPYRIGHT = "Copyright (c) 2026 Convey Agent"
DEFAULT_UPDATE_REPOSITORY = "mohdghousetiltil/convey-ai-agent"
DEFAULT_CLOUD_BACKEND_URL = "https://convey-ai-agent-production.up.railway.app"
DEFAULT_CLOUD_SYNC_URL = f"{DEFAULT_CLOUD_BACKEND_URL}/api/sync"

# AI models. Everything runs through OpenRouter; there is no provider or model choice.
# Document extraction: many calls per matter, so a low-cost model.
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-haiku-5.5"
# Assistant chat: low cost, reliable tool use for searching and reading pages.
# Chosen over Sonnet 5.5 (about 20x the price) and DeepSeek V4.1 Flash (cheaper,
# but leaked internal field names and dropped figures in side-by-side tests).
DEFAULT_CHAT_MODEL = "anthropic/claude-haiku-5.5"
# Used by OpenRouter only if the chat model is unavailable.
CHAT_FALLBACK_MODEL = "openai/gpt-5.6-luna"

# One embedding model on both routes, so vectors stay comparable. 1536 dimensions
# matches the vector(1536) column in the memories table.
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
OPENROUTER_EMBEDDING_MODEL = "openai/text-embedding-3-large"
EMBEDDING_DIMENSIONS = 1536


def get_app_version() -> str:
    env_version = os.getenv("TRICONVEY_APP_VERSION")
    if env_version:
        return env_version.strip()

    # Prefer pyproject.toml when running from source (or when bundled with it),
    # so stale egg-info metadata does not leak into the displayed version.
    candidate_pyprojects: list[Path] = []
    try:
        candidate_pyprojects.append(Path(__file__).resolve().parents[2] / "pyproject.toml")
    except Exception:
        pass

    # PyInstaller frozen builds extract bundled files under sys._MEIPASS.
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate_pyprojects.insert(0, Path(meipass) / "pyproject.toml")

    for pyproject in candidate_pyprojects:
        try:
            if pyproject.exists():
                match = re.search(
                    r'(?m)^version\s*=\s*"([^"]+)"\s*$',
                    pyproject.read_text(encoding="utf-8"),
                )
                if match:
                    return match.group(1)
        except Exception:
            continue
    try:
        return metadata.version("triconvey-agent")
    except metadata.PackageNotFoundError:
        return FALLBACK_APP_VERSION
