from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths, prune_old_directories


class TestBackendRuntime(unittest.TestCase):
    def test_prune_old_directories_keeps_recent_and_latest(self):
        runtime = ensure_runtime_dirs(get_runtime_paths())
        root = runtime.pytest_temp_dir / f"runtime-test-{uuid4().hex}"
        root.mkdir(parents=True, exist_ok=True)
        try:
            old_one = root / "old-one"
            old_two = root / "old-two"
            recent = root / "recent"
            for path in (old_one, old_two, recent):
                path.mkdir(parents=True, exist_ok=True)
                (path / "artifact.txt").write_text("x", encoding="utf-8")

            old_time = (datetime.now(UTC) - timedelta(hours=120)).timestamp()
            recent_time = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
            for path in (old_one, old_two):
                Path(path).touch()
                for child in path.rglob("*"):
                    child.touch()
                import os
                os.utime(path, (old_time, old_time))
            import os
            os.utime(recent, (recent_time, recent_time))

            removed = prune_old_directories(root, max_age_hours=72, keep_latest=1)

            self.assertEqual(len(removed), 2)
            self.assertTrue(recent.exists())
            self.assertEqual(sum(1 for path in root.iterdir() if path.is_dir()), 1)
        finally:
            for child in sorted(root.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            if root.exists():
                root.rmdir()


if __name__ == "__main__":
    unittest.main()
