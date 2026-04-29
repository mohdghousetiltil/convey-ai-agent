from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock, patch


class _FakeBackgroundTasks:
    def __init__(self, session) -> None:
        self.session = session
        self.calls: list[tuple[object, tuple, dict]] = []

    def add_task(self, func, *args, **kwargs) -> None:
        # The run row must be committed before the background worker is queued.
        if not self.session.committed:
            raise AssertionError("background task queued before session.commit()")
        self.calls.append((func, args, kwargs))


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


class TestCreateRunCommitOrdering(unittest.IsolatedAsyncioTestCase):
    async def test_create_run_commits_before_queueing_background_work(self) -> None:
        jose_stub = SimpleNamespace(JWTError=Exception, jwt=SimpleNamespace())
        jose_exceptions_stub = SimpleNamespace(JWTError=Exception)
        tenacity_stub = SimpleNamespace(
            AsyncRetrying=object,
            RetryError=Exception,
            stop_after_attempt=lambda *args, **kwargs: None,
            wait_exponential=lambda *args, **kwargs: None,
        )
        with patch.dict(
            sys.modules,
            {
                "jose": jose_stub,
                "jose.exceptions": jose_exceptions_stub,
                "tenacity": tenacity_stub,
            },
        ):
            from triconvey_agent.backend import api

            session = _FakeSession()
            background_tasks = _FakeBackgroundTasks(session)
            ctx = SimpleNamespace(client=SimpleNamespace(id=uuid.uuid4()), user=SimpleNamespace(id=uuid.uuid4()))

            tmp_dir = Path("tmp_test_run_creation_background_commit")
            tmp_dir.mkdir(parents=True, exist_ok=True)
            try:
                runtime = SimpleNamespace(ui_runs_dir=tmp_dir)
                saved_pdf = tmp_dir / "sample.pdf"
                saved_pdf.write_bytes(b"%PDF-1.4")

                with (
                    patch.object(api, "_persist_uploaded_files", AsyncMock(return_value=([saved_pdf], []))),
                    patch.object(api, "ensure_runtime_dirs", return_value=runtime),
                    patch.object(api, "load_local_settings", return_value={"defaultModelName": "gpt-4.1-mini", "triconveyPath": None}),
                    patch.object(api.RunRepo, "create", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))),
                ):
                    result = await api.create_run(
                        background_tasks=background_tasks,
                        files=[SimpleNamespace(filename="sample.pdf")],
                        use_ai_review=False,
                        model="gpt-4.1-mini",
                        triconvey_exe=None,
                        reanalyse_run_id=None,
                        ctx=ctx,
                        session=session,
                    )
            finally:
                try:
                    if saved_pdf.exists():
                        saved_pdf.unlink()
                except OSError:
                    pass
                try:
                    tmp_dir.rmdir()
                except OSError:
                    pass

            self.assertTrue(session.committed)
            self.assertEqual(result["status"], "pending")
            self.assertEqual(len(background_tasks.calls), 1)


if __name__ == "__main__":
    unittest.main()
