from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import sys


@dataclass(frozen=True)
class AppRuntimePaths:
    repo_root: Path
    bundle_root: Path
    local_app_dir: Path
    cache_dir: Path
    temp_dir: Path
    temp_corpus_dir: Path
    temp_ocr_dir: Path
    pytest_cache_dir: Path
    pytest_temp_dir: Path
    output_dir: Path
    ui_runs_dir: Path
    yaml_dir: Path
    ui_dist_dir: Path
    settings_dir: Path
    settings_file: Path
    env_file: Path


def get_runtime_paths() -> AppRuntimePaths:
    if getattr(sys, "frozen", False):
        local_app_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TriConveyAgent"
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        writable_root = local_app_dir
    else:
        bundle_root = Path(__file__).resolve().parents[3]
        writable_root = bundle_root
        # In source/dev mode keep local app settings inside the workspace so the
        # app remains runnable without elevated permissions. The packaged desktop
        # app still uses %LOCALAPPDATA%\TriConveyAgent on client machines.
        local_app_dir = writable_root / ".local_app"

    cache_dir = writable_root / ".cache"
    return AppRuntimePaths(
        repo_root=writable_root,
        bundle_root=bundle_root,
        local_app_dir=local_app_dir,
        cache_dir=cache_dir,
        temp_dir=cache_dir / "temp",
        temp_corpus_dir=cache_dir / "temp" / "corpus",
        temp_ocr_dir=cache_dir / "temp" / "ocr",
        pytest_cache_dir=cache_dir / "pytest",
        pytest_temp_dir=cache_dir / "pytest" / "tmp",
        output_dir=writable_root / "output",
        ui_runs_dir=writable_root / "output" / "ui_runs",
        yaml_dir=bundle_root / "triconvey-mapping",
        ui_dist_dir=bundle_root / "ui" / "dist",
        settings_dir=local_app_dir / "config",
        settings_file=local_app_dir / "config" / "settings.json",
        env_file=local_app_dir / ".env",
    )


def ensure_runtime_dirs(paths: AppRuntimePaths | None = None) -> AppRuntimePaths:
    paths = paths or get_runtime_paths()
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.temp_dir.mkdir(parents=True, exist_ok=True)
    paths.temp_corpus_dir.mkdir(parents=True, exist_ok=True)
    paths.temp_ocr_dir.mkdir(parents=True, exist_ok=True)
    paths.pytest_cache_dir.mkdir(parents=True, exist_ok=True)
    paths.pytest_temp_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.ui_runs_dir.mkdir(parents=True, exist_ok=True)
    paths.local_app_dir.mkdir(parents=True, exist_ok=True)
    paths.settings_dir.mkdir(parents=True, exist_ok=True)
    try:
        prune_old_directories(paths.temp_corpus_dir, max_age_hours=24, keep_latest=0)
        prune_old_directories(paths.temp_ocr_dir, max_age_hours=24, keep_latest=0)
    except Exception:
        pass
    return paths


def prune_old_directories(
    base_dir: str | Path,
    *,
    max_age_hours: int,
    keep_latest: int = 0,
) -> list[Path]:
    root = Path(base_dir)
    if not root.exists():
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    directories = [path for path in root.iterdir() if path.is_dir()]
    directories.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    protected = {path.resolve() for path in directories[: max(keep_latest, 0)]}
    removed: list[Path] = []

    for path in directories:
        if path.resolve() in protected:
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified_at >= cutoff:
            continue
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        path.rmdir()
        removed.append(path)

    return removed
