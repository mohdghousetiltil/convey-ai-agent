from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from triconvey_agent.backend.runtime import AppRuntimePaths, ensure_runtime_dirs, get_runtime_paths
from triconvey_agent.backend.settings import load_local_settings, save_local_settings


def _runtime(root: Path) -> AppRuntimePaths:
    local = root / "local_app"
    return AppRuntimePaths(
        repo_root=root,
        bundle_root=root,
        local_app_dir=local,
        cache_dir=root / ".cache",
        temp_dir=root / ".cache" / "temp",
        temp_corpus_dir=root / ".cache" / "temp" / "corpus",
        temp_ocr_dir=root / ".cache" / "temp" / "ocr",
        pytest_cache_dir=root / ".cache" / "pytest",
        pytest_temp_dir=root / ".cache" / "pytest" / "tmp",
        output_dir=root / "output",
        ui_runs_dir=root / "output" / "ui_runs",
        yaml_dir=root / "yaml",
        ui_dist_dir=root / "ui" / "dist",
        settings_dir=local / "config",
        settings_file=local / "config" / "settings.json",
        env_file=local / ".env",
    )


class TestBackendSettings(unittest.TestCase):
    def test_save_and_load_local_settings(self):
        base = ensure_runtime_dirs(get_runtime_paths()).pytest_temp_dir / f"settings-test-{uuid4().hex}"
        runtime = _runtime(base)
        try:
            saved = save_local_settings(
                {
                    "language": "English",
                    "openAiApiKey": "sk-test",
                    "defaultModelName": "gpt-4.1-mini",
                    "triconveyPath": r"C:\Program Files\TriConvey\TriConvey.exe",
                },
                runtime,
            )

            loaded = load_local_settings(runtime)

            self.assertEqual(saved["openAiApiKey"], "sk-test")
            self.assertEqual(loaded["openAiApiKey"], "sk-test")
            self.assertEqual(loaded["defaultModelName"], "gpt-4.1-mini")
            self.assertTrue(runtime.env_file.exists())
            self.assertTrue(runtime.settings_file.exists())
        finally:
            if base.exists():
                for child in sorted(base.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        child.rmdir()
                base.rmdir()

    def test_preferred_autofill_fields_are_scoped_per_user(self):
        base = ensure_runtime_dirs(get_runtime_paths()).pytest_temp_dir / f"settings-user-scope-{uuid4().hex}"
        runtime = _runtime(base)
        try:
            first_user = str(uuid4())
            second_user = str(uuid4())

            save_local_settings(
                {
                    "language": "English",
                    "preferredAutofillFields": ["policy_1_certs_attached", "policy_1_total_does_not_exceed"],
                },
                runtime,
                user_id=first_user,
            )
            save_local_settings(
                {
                    "language": "English",
                    "preferredAutofillFields": ["policy_6_attachments"],
                },
                runtime,
                user_id=second_user,
            )

            first_loaded = load_local_settings(runtime, user_id=first_user)
            second_loaded = load_local_settings(runtime, user_id=second_user)

            self.assertEqual(
                first_loaded["preferredAutofillFields"],
                ["policy_1_certs_attached", "policy_1_total_does_not_exceed"],
            )
            self.assertEqual(second_loaded["preferredAutofillFields"], ["policy_6_attachments"])
        finally:
            if base.exists():
                for child in sorted(base.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        child.rmdir()
                base.rmdir()


if __name__ == "__main__":
    unittest.main()
