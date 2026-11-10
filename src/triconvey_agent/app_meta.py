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

# AI models. OpenRouter is the default route, so these are the ids that matter most.
# The app chooses the model; there is no user-facing picker.
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-haiku-5.5"
_PROVIDER_MODELS = {
    "openrouter": DEFAULT_OPENROUTER_MODEL,
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-haiku-5-5",
    "google": "gemini-2.5-flash",
    "hybrid": "gpt-4.1-mini",
}

# One embedding model on both routes, so vectors stay comparable. 1536 dimensions
# matches the vector(1536) column in the memories table.
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
OPENROUTER_EMBEDDING_MODEL = "openai/text-embedding-3-large"
EMBEDDING_DIMENSIONS = 1536


def model_for_provider(provider: str) -> str:
    """Return the model the app uses for a provider."""
    return _PROVIDER_MODELS.get((provider or "").strip().lower(), DEFAULT_OPENROUTER_MODEL)


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
