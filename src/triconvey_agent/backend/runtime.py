from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import shutil
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
    try:
        enforce_storage_quota(paths.local_app_dir)
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


_STORAGE_QUOTA_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

# Subdirectory names (relative to local_app_dir) that must never be deleted.
_PROTECTED_SUBDIRS = frozenset({"config", "updates"})

# File names (anywhere under local_app_dir) that must never be deleted.
_PROTECTED_FILES = frozenset({
    "auth_state.json",
    "settings.json",
    ".env",
    "vector_memory.db",
    "learning_profile.json",
})


def _dir_size(root: Path) -> int:
    """Return total byte size of all files under root."""
    total = 0
    for f in root.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _is_protected(path: Path, local_app_dir: Path) -> bool:
    """Return True if path must not be deleted."""
    if path.name in _PROTECTED_FILES:
        return True
    try:
        rel = path.relative_to(local_app_dir)
    except ValueError:
        return True
    if rel.parts and rel.parts[0] in _PROTECTED_SUBDIRS:
        return True
    return False


def enforce_storage_quota(
    local_app_dir: Path,
    *,
    quota_bytes: int = _STORAGE_QUOTA_BYTES,
) -> int:
    """Delete oldest files/run-folders from local_app_dir until total size is under quota_bytes.

    Deletion order:
      1. Whole run folders inside ui_runs/ (oldest mtime first)
      2. Individual log / debug files (*.log, *.jsonl, *.txt) oldest-first
      3. Remaining non-protected files oldest-first

    Returns the number of bytes freed.
    """
    if not local_app_dir.exists():
        return 0

    total = _dir_size(local_app_dir)
    if total <= quota_bytes:
        return 0

    freed = 0

    def _delete_dir(d: Path) -> int:
        size = _dir_size(d)
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
        return size

    def _delete_file(f: Path) -> int:
        try:
            size = f.stat().st_size
        except OSError:
            return 0
        try:
            f.unlink(missing_ok=True)
        except Exception:
            return 0
        return size

    # --- Pass 1: whole ui_runs directories, oldest first ---
    ui_runs = local_app_dir / "output" / "ui_runs"
    if ui_runs.is_dir():
        run_dirs = sorted(
            [d for d in ui_runs.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
        )
        for run_dir in run_dirs:
            if total - freed <= quota_bytes:
                break
            freed += _delete_dir(run_dir)

    if total - freed <= quota_bytes:
        return freed

    # --- Pass 2: log / debug files oldest-first ---
    log_suffixes = {".log", ".jsonl", ".txt"}
    log_files = sorted(
        [
            f for f in local_app_dir.rglob("*")
            if f.is_file()
            and f.suffix.lower() in log_suffixes
            and not _is_protected(f, local_app_dir)
        ],
        key=lambda f: f.stat().st_mtime,
    )
    for f in log_files:
        if total - freed <= quota_bytes:
            break
        freed += _delete_file(f)

    if total - freed <= quota_bytes:
        return freed

    # --- Pass 3: any remaining non-protected files oldest-first ---
    all_files = sorted(
        [
            f for f in local_app_dir.rglob("*")
            if f.is_file() and not _is_protected(f, local_app_dir)
        ],
        key=lambda f: f.stat().st_mtime,
    )
    for f in all_files:
        if total - freed <= quota_bytes:
            break
        freed += _delete_file(f)

    return freed
