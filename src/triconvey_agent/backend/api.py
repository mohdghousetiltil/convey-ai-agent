from __future__ import annotations

# Load .env variables BEFORE any imports that depend on environment variables
from dotenv import load_dotenv
load_dotenv()

import logging
import os
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.auth.deps import AuthContext, require_auth
from triconvey_agent.backend.auth_api import router as auth_router
from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths
from triconvey_agent.backend.settings import apply_local_settings_env, load_local_settings, save_local_settings
from triconvey_agent.backend.service import (
    ask_run_question,
    autofill_run,
    build_review_run,
    ensure_local_convey_running,
    load_run_payload,
    save_review_answers,
)
from triconvey_agent.db.repositories import (
    AnswerRepo,
    ClientRepo,
    RunRepo,
    SyncQueueRepo,
)
from triconvey_agent.db.session import dispose_engine, get_session
from triconvey_agent.sync.worker import SyncWorker, start_sync_worker

LOG = logging.getLogger(__name__)


class ReviewAnswerUpdate(BaseModel):
    value: Any = None
    needs_review: bool = False


class SaveAnswersRequest(BaseModel):
    updates: dict[str, ReviewAnswerUpdate] = Field(default_factory=dict)


class AutofillRequest(BaseModel):
    dry_run: bool = False
    triconvey_exe: str | None = None
    skip_review_gate: bool = False


class AutofillJobRecord(BaseModel):
    job_id: str
    run_id: str
    status: str
    dry_run: bool = False
    skip_review_gate: bool = False
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


class CreateRunResult(BaseModel):
    run: dict[str, Any]
    convey_launch_attempted: bool = False
    convey_launch_ok: bool | None = None


class ChatRequest(BaseModel):
    question: str
    model: str = "gpt-4.1-mini"


class LocalSettingsPayload(BaseModel):
    language: str = "English"
    openAiApiKey: str = ""
    defaultModelName: str = "gpt-4.1-mini"
    triconveyPath: str = ""


_sync_worker_ref: dict[str, SyncWorker | None] = {"worker": None}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the cloud sync worker on boot; shut it down cleanly."""
    apply_local_settings_env()
    client_slug = os.getenv("CONVEY_CLIENT_SLUG", "").strip()
    if client_slug:
        # Resolve the local client_id for the sync worker.
        from triconvey_agent.db.session import get_session_factory

        factory = get_session_factory()
        try:
            async with factory() as session:
                client = await ClientRepo.get_by_slug(session, client_slug)
                if client is not None:
                    _sync_worker_ref["worker"] = await start_sync_worker(client.id)
                else:
                    LOG.warning(
                        "CONVEY_CLIENT_SLUG=%s has no matching `clients` row; "
                        "run the bootstrap script to create it.",
                        client_slug,
                    )
        except Exception as exc:  # pragma: no cover - surfaced in logs
            LOG.exception("Failed to start sync worker: %s", exc)
    else:
        LOG.info("CONVEY_CLIENT_SLUG not set — sync worker not started.")

    try:
        yield
    finally:
        worker = _sync_worker_ref.get("worker")
        if worker is not None:
            await worker.stop()
        await dispose_engine()


app = FastAPI(title="Convey Agent Backend API", version="0.1.0", lifespan=_lifespan)
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
app.include_router(auth_router)

_ui_dist_dir = get_runtime_paths().ui_dist_dir
_autofill_jobs: dict[str, AutofillJobRecord] = {}
_autofill_cancel_events: dict[str, threading.Event] = {}
_autofill_lock = threading.Lock()
apply_local_settings_env()
if _ui_dist_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_ui_dist_dir / "assets")), name="ui-assets")


@app.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Unauthenticated health check.

    Reports DB reachability so operators can tell local Postgres is up.
    """
    runtime = ensure_runtime_dirs()
    db_ok = True
    db_error: str | None = None
    pending_sync: int | None = None
    try:
        # Trivial query to verify DB.
        from sqlalchemy import text as _text

        await session.execute(_text("SELECT 1"))
        client_slug = os.getenv("CONVEY_CLIENT_SLUG", "").strip()
        if client_slug:
            client = await ClientRepo.get_by_slug(session, client_slug)
            if client is not None:
                pending_sync = await SyncQueueRepo.pending_count(session, client.id)
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    return {
        "ok": db_ok,
        "db_ok": db_ok,
        "db_error": db_error,
        "pending_sync_events": pending_sync,
        "sync_worker_running": _sync_worker_ref.get("worker") is not None,
        "repo_root": str(runtime.repo_root),
        "bundle_root": str(runtime.bundle_root),
        "ui_runs_dir": str(runtime.ui_runs_dir),
        "ui_dist_dir": str(_ui_dist_dir),
    }


@app.get("/api/settings")
def get_settings() -> dict[str, str]:
    return load_local_settings()


@app.post("/api/settings")
def post_settings(body: LocalSettingsPayload) -> dict[str, str]:
    return save_local_settings(body.model_dump(mode="json"))


@app.get("/")
def ui_index() -> FileResponse:
    if not _ui_dist_dir.exists():
        raise HTTPException(status_code=404, detail="Built UI bundle was not found. Run the UI build first.")
    index_file = _ui_dist_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Built UI index.html was not found.")
    return FileResponse(index_file)


@app.post("/api/runs")
async def create_run(
    files: list[UploadFile] = File(...),
    use_ai_review: bool = Form(False),
    model: str = Form("gpt-4.1-mini"),
    launch_convey: bool = Form(True),
    triconvey_exe: str | None = Form(None),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Upload documents, run extraction pipeline, persist results to DB + files."""
    import asyncio
    import uuid as _uuid
    from functools import partial

    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF is required.")

    runtime = ensure_runtime_dirs()
    run_uuid = _uuid.uuid4()
    target_dir = runtime.ui_runs_dir / str(run_uuid)
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

    saved_settings = load_local_settings()
    resolved_model = model or saved_settings["defaultModelName"]
    resolved_triconvey_exe = triconvey_exe or saved_settings["triconveyPath"] or None

    # Create DB run row immediately (status=pending) so we have a FK anchor.
    run_row = await RunRepo.create(
        session,
        client_id=ctx.client.id,
        run_id=run_uuid,
        model=resolved_model,
        use_ai_review=use_ai_review,
    )

    convey_launch_ok: bool | None = None
    if launch_convey:
        convey_launch_ok = ensure_local_convey_running(triconvey_exe=resolved_triconvey_exe)

    # Run the sync-heavy pipeline in a thread pool (keeps event loop free).
    try:
        loop = asyncio.get_event_loop()
        pipeline_fn = partial(
            build_review_run,
            saved_paths,
            run_dir=target_dir,
            use_ai_review=use_ai_review,
            model=resolved_model,
        )
        payload: dict[str, Any] = await loop.run_in_executor(None, pipeline_fn)
    except Exception as exc:
        await RunRepo.update_status(
            session,
            client_id=ctx.client.id,
            run_id=run_uuid,
            status="failed",
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Persist pipeline output to DB (non-blocking, runs in same async context).
    await _persist_run_to_db(session, payload, client_id=ctx.client.id, run_uuid=run_uuid)

    payload["convey_launch_attempted"] = bool(launch_convey)
    payload["convey_launch_ok"] = convey_launch_ok
    return payload


@app.get("/api/runs/{run_id}")
async def get_run(
    run_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    import uuid as _uuid

    # Verify ownership via DB if run_id is a valid UUID.
    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        pass  # legacy string run_id — skip DB ownership check
    run_dir = _resolve_run_dir(run_id)
    return load_run_payload(run_dir)


@app.post("/api/runs/{run_id}/answers")
async def save_answers(
    run_id: str,
    body: SaveAnswersRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Persist human edits: local Postgres (fast, offline-tolerant) + sync queue.

    Also writes the file-based `answers.json` so the existing pipeline/autofill
    code continues to work. Remove the file write once the pipeline reads from DB.
    """
    import uuid as _uuid

    run_dir = _resolve_run_dir(run_id)
    updates = {qid: update.model_dump(mode="json") for qid, update in body.updates.items()}

    # 1) Write to local DB (authoritative source going forward).
    try:
        run_uuid = _uuid.UUID(run_id)
    except ValueError:
        run_uuid = None  # legacy string run_id; DB path is skipped

    if run_uuid is not None:
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found for this client.")
        for question_id, payload in updates.items():
            await AnswerRepo.apply_human_edit(
                session,
                client_id=ctx.client.id,
                run_id=run_uuid,
                question_id=question_id,
                user_id=ctx.user.id,
                new_value=payload.get("value"),
                needs_review=payload.get("needs_review"),
            )

    # 2) Mirror to file-based storage so the existing autofill pipeline keeps working.
    return save_review_answers(run_dir, updates)


@app.post("/api/runs/{run_id}/chat")
def chat_about_run(run_id: str, body: ChatRequest) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    try:
        saved_settings = load_local_settings()
        return ask_run_question(run_dir, question=body.question, model=body.model or saved_settings["defaultModelName"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - surfaced to UI
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/autofill")
def start_autofill(run_id: str, body: AutofillRequest) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    try:
        saved_settings = load_local_settings()
        return autofill_run(
            run_dir,
            dry_run=body.dry_run,
            triconvey_exe=body.triconvey_exe or saved_settings["triconveyPath"] or None,
            skip_review_gate=body.skip_review_gate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - surfaced to UI
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/autofill/start")
async def start_autofill_job(
    run_id: str,
    body: AutofillRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a DB-backed autofill job and run it in a background thread."""
    import uuid as _uuid

    run_dir = _resolve_run_dir(run_id)
    saved_settings = load_local_settings()

    # Persist job to DB.
    from triconvey_agent.db.repositories import AutofillJobRepo

    job = await AutofillJobRepo.create(
        session,
        run_id=_uuid.UUID(run_id) if _is_uuid(run_id) else _uuid.uuid4(),
        dry_run=body.dry_run,
        skip_review_gate=body.skip_review_gate,
        triconvey_exe=body.triconvey_exe or saved_settings["triconveyPath"] or None,
    )
    job_id_str = str(job.id)

    cancel_event = threading.Event()
    # Also keep in-memory for backward-compat with the legacy get/cancel endpoints.
    record = AutofillJobRecord(
        job_id=job_id_str,
        run_id=run_id,
        status="queued",
        dry_run=body.dry_run,
        skip_review_gate=body.skip_review_gate,
        created_at=_utc_now(),
    )
    with _autofill_lock:
        _autofill_jobs[job_id_str] = record
        _autofill_cancel_events[job_id_str] = cancel_event

    worker = threading.Thread(
        target=_run_autofill_job,
        args=(job_id_str, job.id, run_dir, body, cancel_event),
        daemon=True,
    )
    worker.start()
    return record.model_dump(mode="json")


@app.get("/api/autofill-jobs/{job_id}")
async def get_autofill_job(
    job_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    import uuid as _uuid

    # Try DB first (authoritative).
    if _is_uuid(job_id):
        from triconvey_agent.db.repositories import AutofillJobRepo

        job = await AutofillJobRepo.get(session, _uuid.UUID(job_id))
        if job is not None:
            # Merge with in-memory record (has result payload which is not in DB).
            with _autofill_lock:
                mem = _autofill_jobs.get(job_id)
            base = {
                "job_id": str(job.id),
                "run_id": str(job.run_id),
                "status": job.status,
                "dry_run": job.dry_run,
                "skip_review_gate": job.skip_review_gate,
                "created_at": job.created_at.isoformat(),
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "error": job.error,
                "result": mem.result if mem else None,
            }
            return base

    with _autofill_lock:
        record = _autofill_jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Autofill job '{job_id}' was not found.")
    return record.model_dump(mode="json")


@app.post("/api/autofill-jobs/{job_id}/cancel")
async def cancel_autofill_job(
    job_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    with _autofill_lock:
        record = _autofill_jobs.get(job_id)
        cancel_event = _autofill_cancel_events.get(job_id)
        if record is None or cancel_event is None:
            raise HTTPException(status_code=404, detail=f"Autofill job '{job_id}' was not found.")
        if record.status in {"completed", "failed", "cancelled"}:
            return record.model_dump(mode="json")
        cancel_event.set()
        record.status = "cancelling"
        _autofill_jobs[job_id] = record
    return record.model_dump(mode="json")


def _resolve_run_dir(run_id: str) -> Path:
    runtime = ensure_runtime_dirs()
    run_dir = runtime.ui_runs_dir / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' was not found.")
    return run_dir


def _new_run_id(base_dir: Path) -> str:
    """Returns a UUID4 string that is unique on disk (collision is astronomically rare)."""
    from uuid import uuid4

    while True:
        candidate = str(uuid4())
        if not (base_dir / candidate).exists():
            return candidate


def _safe_filename(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")


def _run_autofill_job(
    job_id: str,
    db_job_id: "uuid.UUID",
    run_dir: Path,
    body: AutofillRequest,
    cancel_event: threading.Event,
) -> None:
    """Background thread: runs autofill and persists results to DB."""
    import asyncio
    import uuid

    from triconvey_agent.db.repositories import AutofillJobRepo
    from triconvey_agent.db.session import get_session_factory

    with _autofill_lock:
        record = _autofill_jobs[job_id]
        record.status = "running"
        record.started_at = _utc_now()
        _autofill_jobs[job_id] = record

    # Persist running state to DB via a new event loop in this thread.
    def _db_update(status: str, **kw: Any) -> None:
        async def _inner() -> None:
            factory = get_session_factory()
            async with factory() as s:
                await AutofillJobRepo.update_status(s, job_id=db_job_id, status=status, **kw)
                await s.commit()
        try:
            asyncio.run(_inner())
        except Exception as db_err:
            LOG.warning("DB update for job %s failed: %s", job_id, db_err)

    _db_update("running", started=True)

    try:
        saved_settings = load_local_settings()
        result = autofill_run(
            run_dir,
            dry_run=body.dry_run,
            triconvey_exe=body.triconvey_exe or saved_settings["triconveyPath"] or None,
            skip_review_gate=body.skip_review_gate,
            cancel_requested=cancel_event.is_set,
        )
        report = result.get("execution_report", {})
        metrics = report.get("metrics", {})
        cancelled = bool(metrics.get("cancelled")) or cancel_event.is_set()
        final_status = "cancelled" if cancelled else "completed"

        totals = {
            "filled": report.get("total_filled", 0),
            "verified": report.get("total_verified", 0),
            "failed": report.get("total_failed", 0),
            "skipped": report.get("total_skipped", 0),
            "pending_review": report.get("total_pending_review", 0),
        }
        _db_update(final_status, completed=True, totals=totals)

        with _autofill_lock:
            record = _autofill_jobs[job_id]
            record.result = result
            record.completed_at = _utc_now()
            record.status = final_status
            _autofill_jobs[job_id] = record

    except Exception as exc:  # pragma: no cover - runtime surfacing
        final_status = "cancelled" if cancel_event.is_set() else "failed"
        _db_update(final_status, completed=True, error=str(exc))
        with _autofill_lock:
            record = _autofill_jobs[job_id]
            record.status = final_status
            record.error = str(exc)
            record.completed_at = _utc_now()
            _autofill_jobs[job_id] = record


async def _persist_run_to_db(
    session: "AsyncSession",
    payload: dict[str, Any],
    *,
    client_id: "uuid.UUID",
    run_uuid: "uuid.UUID",
) -> None:
    """After the sync pipeline completes, persist its output to the DB.

    Documents, facts, and answers are inserted in bulk. The file artifacts
    (JSON files) remain as-is on disk — they are the pipeline's own output.
    """
    import uuid
    from datetime import UTC, datetime

    from triconvey_agent.db.repositories import (
        AnswerRepo,
        DocumentRepo,
        FactRepo,
        RunRepo,
    )

    manifest = payload.get("manifest", {})
    metrics = payload.get("metrics", {})
    doc_count: int = manifest.get("document_count", 0)
    total_facts: int = manifest.get("total_facts", 0)

    # 1. Update run row with final status + metrics.
    await RunRepo.update_status(
        session,
        client_id=client_id,
        run_id=run_uuid,
        status="complete",
        completed=True,
        metrics=metrics,
        summary_text=payload.get("summary_text"),
        document_count=doc_count,
        total_facts=total_facts,
    )

    # 2. Insert document metadata rows (one per uploaded file).
    #    The pipeline output doesn't expose individual file metadata directly,
    #    so we read the manifest's document list from disk if available.
    try:
        run_dir = Path(payload.get("run_dir", ""))
        facts_path = run_dir / "facts.json"
        if facts_path.exists():
            import json as _json

            raw_facts = _json.loads(facts_path.read_text(encoding="utf-8"))
            docs_meta = raw_facts.get("documents", [])
            if docs_meta:
                doc_rows = [
                    {
                        "filename": d.get("file", "unknown"),
                        "file_type": "pdf" if d.get("file", "").endswith(".pdf") else "yaml",
                        "document_type": d.get("document_type", "unknown"),
                        "page_count": d.get("page_count"),
                        "char_count": d.get("char_count"),
                        "classification_confidence": d.get("classification_confidence"),
                        "metadata_json": {},
                    }
                    for d in docs_meta
                ]
                await DocumentRepo.create_batch(session, run_id=run_uuid, documents=doc_rows)

            # 3. Insert facts (winning facts only to keep DB lean; full facts in JSON).
            facts_dict: dict[str, list[dict[str, Any]]] = raw_facts.get("facts", {})
            fact_rows: list[dict[str, Any]] = []
            for path_key, path_facts in facts_dict.items():
                for f in path_facts:
                    fact_rows.append({
                        "path": path_key,
                        "value_json": f.get("value"),
                        "value_type": type(f.get("value")).__name__,
                        "confidence": float(f.get("confidence", 0.0)),
                        "extractor": f.get("extractor", "unknown"),
                        "extracted_at": datetime.fromisoformat(f["extracted_at"])
                        if f.get("extracted_at")
                        else datetime.now(UTC),
                        "notes": f.get("notes"),
                        "is_winning": bool(f.get("is_winning", False)),
                        "sources": [
                            {
                                "file": s.get("file", ""),
                                "page": s.get("page"),
                                "quote": s.get("quote"),
                                "quote_verified": bool(s.get("quote_verified", False)),
                                "extractor_note": s.get("extractor_note"),
                            }
                            for s in (f.get("sources") or [])
                        ],
                    })
            if fact_rows:
                await FactRepo.bulk_insert(session, run_id=run_uuid, facts=fact_rows)

    except Exception as exc:
        LOG.warning("Could not read facts.json for DB persistence: %s", exc)

    # 4. Insert answers (all tabs flattened).
    try:
        answer_rows: list[dict[str, Any]] = []
        for tab in payload.get("tabs", []):
            tab_name = tab.get("tab", "")
            for item in tab.get("items", []):
                qid = item.get("question_id", "")
                if not qid:
                    continue
                answer_rows.append({
                    "question_id": qid,
                    "tab": tab_name,
                    "label": item.get("label", qid),
                    "expected_type": item.get("expected_type"),
                    "answer_strategy": item.get("answer_strategy"),
                    "options": item.get("options") or None,
                    "description": item.get("description"),
                    "value_json": item.get("value"),
                    "confidence": float(item.get("confidence", 0.0)),
                    "facts_used": item.get("facts_used") or None,
                    "needs_review": bool(item.get("needs_review", False)),
                    "review_reasons": item.get("review_reasons") or None,
                    "presentation_hints": item.get("presentation_hints") or {},
                })
        if answer_rows:
            await AnswerRepo.bulk_upsert(
                session, client_id=client_id, run_id=run_uuid, answers=answer_rows
            )
    except Exception as exc:
        LOG.warning("Could not persist answers to DB: %s", exc)


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
