from __future__ import annotations

from importlib import metadata

APP_NAME = "Convey Agent"
APP_SLUG = "TriConveyAgent"
APP_EXECUTABLE_NAME = "TriConveyAgent.exe"
APP_PUBLISHER = "Convey Agent"
APP_COPYRIGHT = "Copyright (c) 2026 Convey Agent"
DEFAULT_UPDATE_REPOSITORY = "mohdghousetiltil/convey-ai-agent"
DEFAULT_CLOUD_BACKEND_URL = "https://convey-ai-agent-production.up.railway.app"
DEFAULT_CLOUD_SYNC_URL = f"{DEFAULT_CLOUD_BACKEND_URL}/api/sync"


def get_app_version() -> str:
    try:
        return metadata.version("triconvey-agent")
    except metadata.PackageNotFoundError:
        return "0.1.0"
