from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from triconvey_agent.backend.runtime import AppRuntimePaths, ensure_runtime_dirs, get_runtime_paths

DEFAULT_LOCAL_SETTINGS = {
    "language": "English",
    "openAiApiKey": "",
    "anthropicApiKey": "",
    "aiProvider": "openai",          # "openai" | "anthropic"
    "defaultModelName": "gpt-4.1-mini",
    "triconveyPath": "",
    "preferredAutofillFields": [],
}


def _normalize_user_key(user_id: str | None) -> str | None:
    key = str(user_id or "").strip()
    return key or None


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

    openai_key = env_values.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key

    anthropic_key = env_values.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        os.environ["ANTHROPIC_API_KEY"] = anthropic_key


def load_local_settings(paths: AppRuntimePaths | None = None, user_id: str | None = None) -> dict[str, Any]:
    runtime = ensure_runtime_dirs(paths or get_runtime_paths())
    payload = dict(DEFAULT_LOCAL_SETTINGS)
    user_key = _normalize_user_key(user_id)

    if runtime.settings_file.exists():
        try:
            raw = json.loads(runtime.settings_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload["language"] = str(raw.get("language") or payload["language"])
                payload["defaultModelName"] = str(raw.get("defaultModelName") or payload["defaultModelName"])
                payload["triconveyPath"] = str(raw.get("triconveyPath") or payload["triconveyPath"])
                payload["aiProvider"] = str(raw.get("aiProvider") or payload["aiProvider"])
                preferred = raw.get("preferredAutofillFields")
                if isinstance(preferred, list):
                    payload["preferredAutofillFields"] = [str(item) for item in preferred if str(item).strip()]
                if user_key:
                    per_user = raw.get("preferredAutofillFieldsByUser")
                    if isinstance(per_user, dict):
                        scoped = per_user.get(user_key)
                        if isinstance(scoped, list):
                            payload["preferredAutofillFields"] = [
                                str(item) for item in scoped if str(item).strip()
                            ]
        except json.JSONDecodeError:
            pass

    env_values = _parse_env_file(runtime.env_file)
    # Prefer the runtime env file; fall back to os.environ (loaded from project .env)
    payload["openAiApiKey"] = env_values.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    payload["anthropicApiKey"] = env_values.get("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY", "")
    return payload


def save_local_settings(
    settings: dict[str, Any],
    paths: AppRuntimePaths | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    runtime = ensure_runtime_dirs(paths or get_runtime_paths())
    current = load_local_settings(runtime, user_id=user_id)
    user_key = _normalize_user_key(user_id)
    raw_settings: dict[str, Any] = {}
    if runtime.settings_file.exists():
        try:
            maybe_raw = json.loads(runtime.settings_file.read_text(encoding="utf-8"))
            if isinstance(maybe_raw, dict):
                raw_settings = maybe_raw
        except json.JSONDecodeError:
            raw_settings = {}

    per_user = raw_settings.get("preferredAutofillFieldsByUser")
    if not isinstance(per_user, dict):
        per_user = {}

    merged = {
        "language": str(settings.get("language") or current["language"] or DEFAULT_LOCAL_SETTINGS["language"]),
        "openAiApiKey": str(settings.get("openAiApiKey") or ""),
        "anthropicApiKey": str(settings.get("anthropicApiKey") or ""),
        "aiProvider": str(settings.get("aiProvider") or current["aiProvider"] or DEFAULT_LOCAL_SETTINGS["aiProvider"]),
        "defaultModelName": str(settings.get("defaultModelName") or current["defaultModelName"] or DEFAULT_LOCAL_SETTINGS["defaultModelName"]),
        "triconveyPath": str(settings.get("triconveyPath") or current["triconveyPath"] or ""),
        "preferredAutofillFields": [
            str(item)
            for item in (settings.get("preferredAutofillFields") or current.get("preferredAutofillFields") or [])
            if str(item).strip()
        ],
    }
    if user_key:
        per_user[user_key] = merged["preferredAutofillFields"]
    elif merged["preferredAutofillFields"]:
        raw_settings["preferredAutofillFields"] = merged["preferredAutofillFields"]

    runtime.settings_file.write_text(
        json.dumps(
            {
                "language": merged["language"],
                "aiProvider": merged["aiProvider"],
                "defaultModelName": merged["defaultModelName"],
                "triconveyPath": merged["triconveyPath"],
                "preferredAutofillFields": raw_settings.get("preferredAutofillFields", []),
                "preferredAutofillFieldsByUser": per_user,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_env_file(
        runtime.env_file,
        {
            "OPENAI_API_KEY": merged["openAiApiKey"],
            "ANTHROPIC_API_KEY": merged["anthropicApiKey"],
        },
    )

    if merged["openAiApiKey"]:
        os.environ["OPENAI_API_KEY"] = merged["openAiApiKey"]
    else:
        os.environ.pop("OPENAI_API_KEY", None)

    if merged["anthropicApiKey"]:
        os.environ["ANTHROPIC_API_KEY"] = merged["anthropicApiKey"]
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)

    return merged
