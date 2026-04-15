from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class AppRuntimePaths:
    repo_root: Path
    cache_dir: Path
    pytest_cache_dir: Path
    pytest_temp_dir: Path
    output_dir: Path
    ui_runs_dir: Path
    yaml_dir: Path


def get_runtime_paths() -> AppRuntimePaths:
    repo_root = Path(__file__).resolve().parents[3]
    cache_dir = repo_root / ".cache"
    return AppRuntimePaths(
        repo_root=repo_root,
        cache_dir=cache_dir,
        pytest_cache_dir=cache_dir / "pytest",
        pytest_temp_dir=cache_dir / "pytest" / "tmp",
        output_dir=repo_root / "output",
        ui_runs_dir=repo_root / "output" / "ui_runs",
        yaml_dir=repo_root / "triconvey-mapping",
    )


def ensure_runtime_dirs(paths: AppRuntimePaths | None = None) -> AppRuntimePaths:
    paths = paths or get_runtime_paths()
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.pytest_cache_dir.mkdir(parents=True, exist_ok=True)
    paths.pytest_temp_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.ui_runs_dir.mkdir(parents=True, exist_ok=True)
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
