from __future__ import annotations

from importlib import metadata

APP_NAME = "TriConvey Agent"
APP_SLUG = "TriConveyAgent"
APP_EXECUTABLE_NAME = "TriConveyAgent.exe"
APP_PUBLISHER = "TriConvey Agent"
APP_COPYRIGHT = "Copyright (c) 2026 TriConvey Agent"
DEFAULT_UPDATE_REPOSITORY = "mohdghousetiltil/convey-ai-agent"


def get_app_version() -> str:
    try:
        return metadata.version("triconvey-agent")
    except metadata.PackageNotFoundError:
        return "0.1.0"
