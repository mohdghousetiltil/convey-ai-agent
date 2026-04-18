from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from triconvey_agent.backend.runtime import AppRuntimePaths, ensure_runtime_dirs, get_runtime_paths

DEFAULT_LOCAL_SETTINGS = {
    "language": "English",
    "openAiApiKey": "",
    "defaultModelName": "gpt-4.1-mini",
    "triconveyPath": "",
}


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    def _escape_env_value(value: str) -> str:
        return value.replace('"', '\\"')

    lines = [f'{key}="{_escape_env_value(value)}"' for key, value in values.items() if value]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def apply_local_settings_env(paths: AppRuntimePaths | None = None) -> None:
    runtime = ensure_runtime_dirs(paths or get_runtime_paths())
    env_values = _parse_env_file(runtime.env_file)
    api_key = env_values.get("OPENAI_API_KEY", "").strip()
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key


def load_local_settings(paths: AppRuntimePaths | None = None) -> dict[str, str]:
    runtime = ensure_runtime_dirs(paths or get_runtime_paths())
    payload = dict(DEFAULT_LOCAL_SETTINGS)

    if runtime.settings_file.exists():
        try:
            raw = json.loads(runtime.settings_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload["language"] = str(raw.get("language") or payload["language"])
                payload["defaultModelName"] = str(raw.get("defaultModelName") or payload["defaultModelName"])
                payload["triconveyPath"] = str(raw.get("triconveyPath") or payload["triconveyPath"])
        except json.JSONDecodeError:
            pass

    env_values = _parse_env_file(runtime.env_file)
    payload["openAiApiKey"] = env_values.get("OPENAI_API_KEY", "")
    return payload


def save_local_settings(settings: dict[str, Any], paths: AppRuntimePaths | None = None) -> dict[str, str]:
    runtime = ensure_runtime_dirs(paths or get_runtime_paths())
    current = load_local_settings(runtime)
    merged = {
        "language": str(settings.get("language") or current["language"] or DEFAULT_LOCAL_SETTINGS["language"]),
        "openAiApiKey": str(settings.get("openAiApiKey") or ""),
        "defaultModelName": str(settings.get("defaultModelName") or current["defaultModelName"] or DEFAULT_LOCAL_SETTINGS["defaultModelName"]),
        "triconveyPath": str(settings.get("triconveyPath") or current["triconveyPath"] or ""),
    }

    runtime.settings_file.write_text(
        json.dumps(
            {
                "language": merged["language"],
                "defaultModelName": merged["defaultModelName"],
                "triconveyPath": merged["triconveyPath"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_env_file(runtime.env_file, {"OPENAI_API_KEY": merged["openAiApiKey"]})
    if merged["openAiApiKey"]:
        os.environ["OPENAI_API_KEY"] = merged["openAiApiKey"]
    else:
        os.environ.pop("OPENAI_API_KEY", None)
    return merged
