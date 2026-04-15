from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths
from triconvey_agent.backend.service import (
    autofill_run,
    build_review_run,
    load_run_payload,
    save_review_answers,
)


class ReviewAnswerUpdate(BaseModel):
    value: Any = None
    needs_review: bool = False


class SaveAnswersRequest(BaseModel):
    updates: dict[str, ReviewAnswerUpdate] = Field(default_factory=dict)


class AutofillRequest(BaseModel):
    dry_run: bool = False
    triconvey_exe: str | None = None
    skip_review_gate: bool = False


app = FastAPI(title="TriConvey Agent Backend API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    runtime = ensure_runtime_dirs()
    return {
        "ok": True,
        "repo_root": str(runtime.repo_root),
        "ui_runs_dir": str(runtime.ui_runs_dir),
    }


@app.post("/api/runs")
async def create_run(
    files: list[UploadFile] = File(...),
    use_ai_review: bool = Form(False),
    model: str = Form("gpt-4.1-mini"),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF is required.")

    runtime = ensure_runtime_dirs()
    target_dir = runtime.ui_runs_dir / _new_run_id(runtime.ui_runs_dir)
    uploads_dir = target_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    for upload in files:
        if not upload.filename:
            continue
        destination = uploads_dir / _safe_filename(upload.filename)
        with destination.open("wb") as output_stream:
            shutil.copyfileobj(upload.file, output_stream)
        saved_paths.append(destination)
        await upload.close()

    if not saved_paths:
        raise HTTPException(status_code=400, detail="No valid files were uploaded.")

    try:
        return build_review_run(
            saved_paths,
            run_dir=target_dir,
            use_ai_review=use_ai_review,
            model=model,
        )
    except Exception as exc:  # pragma: no cover - surfaced to UI
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    return load_run_payload(run_dir)


@app.post("/api/runs/{run_id}/answers")
def save_answers(run_id: str, body: SaveAnswersRequest) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    updates = {qid: update.model_dump(mode="json") for qid, update in body.updates.items()}
    return save_review_answers(run_dir, updates)


@app.post("/api/runs/{run_id}/autofill")
def start_autofill(run_id: str, body: AutofillRequest) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    try:
        return autofill_run(
            run_dir,
            dry_run=body.dry_run,
            triconvey_exe=body.triconvey_exe,
            skip_review_gate=body.skip_review_gate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - surfaced to UI
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _resolve_run_dir(run_id: str) -> Path:
    runtime = ensure_runtime_dirs()
    run_dir = runtime.ui_runs_dir / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' was not found.")
    return run_dir


def _new_run_id(base_dir: Path) -> str:
    from datetime import UTC, datetime
    from uuid import uuid4

    while True:
        candidate = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
        if not (base_dir / candidate).exists():
            return candidate


def _safe_filename(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")
