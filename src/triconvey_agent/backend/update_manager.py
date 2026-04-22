from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from triconvey_agent.app_meta import APP_EXECUTABLE_NAME, APP_NAME, DEFAULT_UPDATE_REPOSITORY
from triconvey_agent.backend.runtime import AppRuntimePaths, ensure_runtime_dirs, get_runtime_paths

GITHUB_API_BASE = "https://api.github.com"
INSTALLER_NAME_HINTS = ("setup", "installer", "triconveyagent", "conveyagent")


def _normalize_repo(repo: str | None) -> str:
    value = (repo or "").strip().strip("/")
    if value.lower().startswith("https://github.com/"):
        value = value[19:]
    if value.lower().endswith(".git"):
        value = value[:-4]
    return value.strip("/")


def _version_key(value: str | None) -> tuple[int, int, int, int]:
    if not value:
        return (0, 0, 0, 0)
    text = str(value).strip().lower().lstrip("v")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$", text)
    if not match:
        return (0, 0, 0, 0)
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    prerelease = 0 if "-" not in text else -1
    return (major, minor, patch, prerelease)


def is_version_newer(candidate: str | None, current: str | None) -> bool:
    return _version_key(candidate) > _version_key(current)


def select_installer_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked: list[tuple[int, dict[str, Any]]] = []
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        lowered = name.lower()
        if not name or not url or not lowered.endswith(".exe"):
            continue
        score = 0
        if "setup" in lowered:
            score += 5
        if "installer" in lowered:
            score += 4
        if APP_EXECUTABLE_NAME.lower().replace(".exe", "") in lowered:
            score += 3
        if any(hint in lowered for hint in INSTALLER_NAME_HINTS):
            score += 1
        ranked.append((score, asset))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME} updater",
    }


def _release_to_payload(
    release: dict[str, Any],
    *,
    current_version: str,
    update_repository: str,
) -> dict[str, Any]:
    tag_name = str(release.get("tag_name") or "").strip()
    latest_version = tag_name.lstrip("v") or None
    installer_asset = select_installer_asset(list(release.get("assets") or []))
    return {
        "enabled": True,
        "current_version": current_version,
        "update_repository": update_repository,
        "checked_at": datetime.now(UTC).isoformat(),
        "update_available": bool(latest_version and is_version_newer(latest_version, current_version)),
        "latest_version": latest_version,
        "release_name": release.get("name") or tag_name or None,
        "release_url": release.get("html_url"),
        "published_at": release.get("published_at"),
        "prerelease": bool(release.get("prerelease")),
        "installer_asset": (
            {
                "name": installer_asset.get("name"),
                "download_url": installer_asset.get("browser_download_url"),
                "size_bytes": installer_asset.get("size"),
            }
            if installer_asset
            else None
        ),
        "notes": release.get("body") or "",
        "error": None,
    }


def check_for_updates(
    *,
    current_version: str,
    update_repository: str | None = None,
    include_prerelease: bool = False,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    repo = _normalize_repo(update_repository or DEFAULT_UPDATE_REPOSITORY)
    payload: dict[str, Any] = {
        "enabled": bool(repo),
        "current_version": current_version,
        "update_repository": repo,
        "checked_at": datetime.now(UTC).isoformat(),
        "update_available": False,
        "latest_version": None,
        "release_name": None,
        "release_url": None,
        "published_at": None,
        "prerelease": False,
        "installer_asset": None,
        "notes": "",
        "error": None,
    }
    if not repo:
        payload["error"] = "Update repository is not configured."
        return payload

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=_headers()) as client:
            if include_prerelease:
                response = client.get(f"{GITHUB_API_BASE}/repos/{repo}/releases")
                if response.status_code == 403:
                    return payload
                response.raise_for_status()
                releases = list(response.json() or [])
                release = next((item for item in releases if not item.get("draft")), None)
                if release is None:
                    payload["error"] = "No published releases were found."
                    return payload
            else:
                response = client.get(f"{GITHUB_API_BASE}/repos/{repo}/releases/latest")
                if response.status_code == 403:
                    return payload
                response.raise_for_status()
                release = response.json()
    except Exception as exc:
        payload["error"] = str(exc)
        return payload

    return _release_to_payload(
        release,
        current_version=current_version,
        update_repository=repo,
    )


def download_update_installer(
    release_payload: dict[str, Any],
    *,
    runtime: AppRuntimePaths | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    runtime = ensure_runtime_dirs(runtime or get_runtime_paths())
    installer = release_payload.get("installer_asset") or {}
    download_url = str(installer.get("download_url") or "").strip()
    asset_name = str(installer.get("name") or "").strip()
    latest_version = str(release_payload.get("latest_version") or "").strip() or "latest"

    if not download_url or not asset_name:
        raise ValueError("No installer asset is available for this release.")

    updates_dir = runtime.local_app_dir / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    target = updates_dir / f"ConveyAgent-Setup-{latest_version}.exe"
    temp_target = target.with_suffix(".download")

    with httpx.stream("GET", download_url, follow_redirects=True, timeout=timeout_seconds, headers=_headers()) as response:
        response.raise_for_status()
        with temp_target.open("wb") as handle:
            for chunk in response.iter_bytes():
                if chunk:
                    handle.write(chunk)

    temp_target.replace(target)
    return {
        "installer_path": str(target),
        "installer_name": asset_name,
        "latest_version": latest_version,
        "downloaded_at": datetime.now(UTC).isoformat(),
    }
