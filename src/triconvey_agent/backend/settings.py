from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from triconvey_agent.app_meta import DEFAULT_UPDATE_REPOSITORY
from triconvey_agent.backend.runtime import AppRuntimePaths, ensure_runtime_dirs, get_runtime_paths

DEFAULT_LOCAL_SETTINGS = {
    "language": "English",
    "openAiApiKey": "",
    "anthropicApiKey": "",
    "aiProvider": "openai",          # "openai" | "anthropic" | "hybrid"
    "aiMode": "cost_efficient",      # "cost_efficient" | "all_time_best" | "turbo"
    "defaultModelName": "gpt-4.1-mini",
    "triconveyPath": "",
    "preferredAutofillFields": [],
    "updateRepository": DEFAULT_UPDATE_REPOSITORY,
    "includePrereleaseUpdates": False,
    "autoCheckForUpdates": True,
    "cloudSyncEnabled": True,
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

    cloud_sync_url = env_values.get("CONVEY_CLOUD_SYNC_URL", "").strip()
    if cloud_sync_url:
        os.environ["CONVEY_CLOUD_SYNC_URL"] = cloud_sync_url

    cloud_sync_token = env_values.get("CONVEY_CLOUD_SYNC_TOKEN", "").strip()
    if cloud_sync_token:
        os.environ["CONVEY_CLOUD_SYNC_TOKEN"] = cloud_sync_token

    client_slug = env_values.get("CONVEY_CLIENT_SLUG", "").strip()
    if client_slug:
        os.environ["CONVEY_CLIENT_SLUG"] = client_slug

    for key in (
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
        "MICROSOFT_TENANT_ID",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
    ):
        value = env_values.get(key, "").strip()
        if value:
            os.environ[key] = value


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
                payload["aiMode"] = str(raw.get("aiMode") or payload["aiMode"])
                payload["updateRepository"] = DEFAULT_UPDATE_REPOSITORY
                payload["includePrereleaseUpdates"] = bool(raw.get("includePrereleaseUpdates", payload["includePrereleaseUpdates"]))
                payload["autoCheckForUpdates"] = bool(raw.get("autoCheckForUpdates", payload["autoCheckForUpdates"]))
                payload["cloudSyncEnabled"] = bool(raw.get("cloudSyncEnabled", payload["cloudSyncEnabled"]))
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
        "aiMode": str(settings.get("aiMode") or current.get("aiMode") or DEFAULT_LOCAL_SETTINGS["aiMode"]),
        "defaultModelName": str(settings.get("defaultModelName") or current["defaultModelName"] or DEFAULT_LOCAL_SETTINGS["defaultModelName"]),
        "triconveyPath": str(settings.get("triconveyPath") or current["triconveyPath"] or ""),
        "updateRepository": DEFAULT_UPDATE_REPOSITORY,
        "includePrereleaseUpdates": bool(settings.get("includePrereleaseUpdates", current.get("includePrereleaseUpdates", DEFAULT_LOCAL_SETTINGS["includePrereleaseUpdates"]))),
        "autoCheckForUpdates": bool(settings.get("autoCheckForUpdates", current.get("autoCheckForUpdates", DEFAULT_LOCAL_SETTINGS["autoCheckForUpdates"]))),
        "cloudSyncEnabled": bool(settings.get("cloudSyncEnabled", current.get("cloudSyncEnabled", DEFAULT_LOCAL_SETTINGS["cloudSyncEnabled"]))),
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
                "aiMode": merged["aiMode"],
                "defaultModelName": merged["defaultModelName"],
                "triconveyPath": merged["triconveyPath"],
                "updateRepository": merged["updateRepository"],
                "includePrereleaseUpdates": merged["includePrereleaseUpdates"],
                "autoCheckForUpdates": merged["autoCheckForUpdates"],
                "cloudSyncEnabled": merged["cloudSyncEnabled"],
                "preferredAutofillFields": raw_settings.get("preferredAutofillFields", []),
                "preferredAutofillFieldsByUser": per_user,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    env_values = _parse_env_file(runtime.env_file)
    env_values["OPENAI_API_KEY"] = merged["openAiApiKey"]
    env_values["ANTHROPIC_API_KEY"] = merged["anthropicApiKey"]
    _write_env_file(runtime.env_file, env_values)

    if merged["openAiApiKey"]:
        os.environ["OPENAI_API_KEY"] = merged["openAiApiKey"]
    else:
        os.environ.pop("OPENAI_API_KEY", None)

    if merged["anthropicApiKey"]:
        os.environ["ANTHROPIC_API_KEY"] = merged["anthropicApiKey"]
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)

    return merged


def load_runtime_env_values(paths: AppRuntimePaths | None = None) -> dict[str, str]:
    runtime = ensure_runtime_dirs(paths or get_runtime_paths())
    return _parse_env_file(runtime.env_file)


def save_runtime_env_values(
    values: dict[str, str | None],
    paths: AppRuntimePaths | None = None,
) -> dict[str, str]:
    runtime = ensure_runtime_dirs(paths or get_runtime_paths())
    existing = _parse_env_file(runtime.env_file)
    for key, value in values.items():
        text = str(value or "").strip()
        if text:
            existing[key] = text
            os.environ[key] = text
        else:
            existing.pop(key, None)
            os.environ.pop(key, None)
    _write_env_file(runtime.env_file, existing)
    return existing
