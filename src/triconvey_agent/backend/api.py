from __future__ import annotations

# Load .env variables BEFORE any imports that depend on environment variables
from dotenv import load_dotenv
load_dotenv()

import logging
import json
import os
import subprocess
import shutil
import threading
import uuid as _uuid
from datetime import datetime, timedelta, UTC
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4
import httpx

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.app_meta import (
    APP_NAME,
    APP_PUBLISHER,
    DEFAULT_CLOUD_BACKEND_URL,
    DEFAULT_CLOUD_SYNC_URL,
    get_app_version,
)
from triconvey_agent.auth.deps import AuthContext, require_auth
from triconvey_agent.backend.auth_api import router as auth_router
from triconvey_agent.backend.sync_api import router as sync_router
from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths
from triconvey_agent.backend.settings import (
    apply_local_settings_env,
    load_local_settings,
    load_runtime_env_values,
    save_local_settings,
    save_runtime_env_values,
)
from triconvey_agent.backend.update_manager import check_for_updates, download_update_installer
from triconvey_agent.backend.service import (
    ask_run_question,
    autofill_run,
    build_review_run,
    check_triconvey_running_passive,
    ensure_local_convey_running,
    load_run_payload,
    save_review_answers,
    set_autofill_activity,
    warm_brain_f_assets_async,
)
from triconvey_agent.brain_f.cache import prime_cached_pdf_analysis
from triconvey_agent.ingest.pdf_loader import load_pdf_document
from triconvey_agent.canonical.questions.loader import load_question_registry
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
    manual_action: dict[str, Any] | None = None


class CreateRunResult(BaseModel):
    run: dict[str, Any]
    convey_launch_attempted: bool = False
    convey_launch_ok: bool | None = None


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    question: str
    model: str | None = None          # if None, uses saved defaultModelName
    history: list[ChatMessage] = Field(default_factory=list)
    mode: str = "standard"            # "quick" | "standard" | "thorough"
    aiMode: str | None = None


class AnswerPatch(BaseModel):
    question_id: str
    new_value: str
    reason: str


class ApplyPatchRequest(BaseModel):
    patches: list[AnswerPatch]


class LocalSettingsPayload(BaseModel):
    language: str = "English"
    openAiApiKey: str = ""
    anthropicApiKey: str = ""
    aiProvider: str = "openai"          # "openai" | "anthropic" | "hybrid"
    aiMode: str = "cost_efficient"      # "cost_efficient" | "all_time_best" | "turbo"
    defaultModelName: str = "gpt-4.1-mini"
    triconveyPath: str = ""
    preferredAutofillFields: list[str] = Field(default_factory=list)
    updateRepository: str = ""
    includePrereleaseUpdates: bool = False
    autoCheckForUpdates: bool = True
    cloudSyncEnabled: bool = True


class UpdateCheckRequest(BaseModel):
    include_prerelease: bool | None = None
    update_repository: str | None = None


class UpdateInstallRequest(BaseModel):
    include_prerelease: bool | None = None
    update_repository: str | None = None


class CloudSyncStatusPayload(BaseModel):
    enabled: bool = True
    connected: bool = False
    configured: bool = False
    cloud_sync_url: str = DEFAULT_CLOUD_SYNC_URL
    client_slug: str | None = None
    worker_running: bool = False
    pending_sync_events: int | None = None
    last_synced_at: str | None = None
    detail: str = ""


_sync_worker_ref: dict[str, SyncWorker | None] = {"worker": None}


async def _ensure_sync_worker_running(client_id: _uuid.UUID) -> bool:
    worker = _sync_worker_ref.get("worker")
    if worker is not None:
        return True
    started = await start_sync_worker(client_id)
    _sync_worker_ref["worker"] = started
    return started is not None


async def _stop_sync_worker() -> None:
    worker = _sync_worker_ref.get("worker")
    if worker is not None:
        await worker.stop()
        _sync_worker_ref["worker"] = None


async def _cloud_sync_status_for_client(
    session: AsyncSession,
    client: Any,
) -> dict[str, Any]:
    env_values = load_runtime_env_values()
    configured = bool(env_values.get("CONVEY_CLOUD_SYNC_TOKEN")) and bool(env_values.get("CONVEY_CLOUD_SYNC_URL"))
    pending_sync = await SyncQueueRepo.pending_count(session, client.id)
    return {
        "enabled": True,
        "connected": configured and (_sync_worker_ref.get("worker") is not None),
        "configured": configured,
        "cloud_sync_url": env_values.get("CONVEY_CLOUD_SYNC_URL") or DEFAULT_CLOUD_SYNC_URL,
        "client_slug": client.slug,
        "worker_running": _sync_worker_ref.get("worker") is not None,
        "pending_sync_events": pending_sync,
        "last_synced_at": client.last_synced_at.isoformat() if client.last_synced_at else None,
        "detail": "Cloud sync connected." if configured else "Cloud sync is not connected yet.",
    }


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


app = FastAPI(title="Convey Agent Backend API", version=get_app_version(), lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8765",
        "http://localhost:8765",
    ],
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(sync_router)

_ui_dist_dir = get_runtime_paths().ui_dist_dir
_autofill_jobs: dict[str, AutofillJobRecord] = {}
_autofill_cancel_events: dict[str, threading.Event] = {}
_autofill_job_context: dict[str, dict[str, Any]] = {}
_autofill_lock = threading.Lock()
apply_local_settings_env()
if _ui_dist_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_ui_dist_dir / "assets")), name="ui-assets")


def _triconvey_import_debug(event: str, **fields: Any) -> None:
    try:
        runtime = ensure_runtime_dirs()
        log_file = runtime.local_app_dir / "triconvey_import_debug.log"
        timestamp = datetime.now(UTC).isoformat()
        parts = [f"ts={timestamp}", f"event={event}"]
        for key, value in fields.items():
            text = str(value).replace("\n", " ").replace("\r", " ")
            parts.append(f"{key}={text}")
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(" | ".join(parts) + "\n")
    except Exception:
        pass


def _force_stop_triconvey() -> bool:
    """Best-effort hard stop for TriConvey when the user cancels autofill."""
    stopped = False
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if "triconvey" not in name:
                continue
            try:
                proc.kill()
                stopped = True
            except Exception:
                continue
    except Exception:
        pass

    if stopped:
        return True

    try:
        completed = subprocess.run(
            ["taskkill", "/F", "/IM", "TriConvey.exe", "/T"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


@app.get("/api/app/runtime-debug")
def get_runtime_debug() -> dict[str, Any]:
    runtime = ensure_runtime_dirs()
    env_values = load_runtime_env_values()
    return {
        "frozen": bool(getattr(os, "frozen", False) or getattr(__import__("sys"), "frozen", False)),
        "runtime_env_file": str(runtime.env_file),
        "runtime_env_exists": runtime.env_file.exists(),
        "program_files_env_exists": Path(os.path.dirname(__import__("sys").executable) if getattr(__import__("sys"), "frozen", False) else ".").joinpath(".env").exists(),
        "oauth": {
            "microsoft_client_id_loaded": bool(env_values.get("MICROSOFT_CLIENT_ID") or os.getenv("MICROSOFT_CLIENT_ID")),
            "microsoft_client_secret_loaded": bool(env_values.get("MICROSOFT_CLIENT_SECRET") or os.getenv("MICROSOFT_CLIENT_SECRET")),
            "microsoft_tenant_id_loaded": bool(env_values.get("MICROSOFT_TENANT_ID") or os.getenv("MICROSOFT_TENANT_ID")),
            "google_client_id_loaded": bool(env_values.get("GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID")),
            "google_client_secret_loaded": bool(env_values.get("GOOGLE_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET")),
        },
    }


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
def get_settings(ctx: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    return load_local_settings(user_id=str(ctx.user.id))


@app.post("/api/settings")
def post_settings(
    body: LocalSettingsPayload,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    return save_local_settings(body.model_dump(mode="json"), user_id=str(ctx.user.id))


@app.get("/api/app/info")
def get_app_info(ctx: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    _ = ctx
    return {
        "name": APP_NAME,
        "publisher": APP_PUBLISHER,
        "version": get_app_version(),
    }


@app.get("/api/cloud-sync/status", response_model=CloudSyncStatusPayload)
async def get_cloud_sync_status(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> CloudSyncStatusPayload:
    return CloudSyncStatusPayload(**await _cloud_sync_status_for_client(session, ctx.client))


@app.post("/api/cloud-sync/bootstrap", response_model=CloudSyncStatusPayload)
async def bootstrap_cloud_sync(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> CloudSyncStatusPayload:
    settings = load_local_settings(user_id=str(ctx.user.id))
    current_status = await _cloud_sync_status_for_client(session, ctx.client)
    if current_status["configured"] and current_status["worker_running"]:
        return CloudSyncStatusPayload(**current_status)
    if current_status["configured"] and not current_status["worker_running"]:
        await _ensure_sync_worker_running(ctx.client.id)
        return CloudSyncStatusPayload(**await _cloud_sync_status_for_client(session, ctx.client))

    if not bool(settings.get("cloudSyncEnabled", True)):
        await _stop_sync_worker()
        save_runtime_env_values({"CONVEY_CLOUD_SYNC_TOKEN": ""})
        status_payload = await _cloud_sync_status_for_client(session, ctx.client)
        status_payload["detail"] = "Cloud sync is disabled for this desktop install."
        return CloudSyncStatusPayload(**status_payload)

    activation_key = (ctx.client.license_key or "").strip()
    if not activation_key:
        status_payload = await _cloud_sync_status_for_client(session, ctx.client)
        status_payload["detail"] = "No activation key is available for automatic cloud sync bootstrap."
        return CloudSyncStatusPayload(**status_payload)

    issue_token_url = f"{DEFAULT_CLOUD_BACKEND_URL}/api/auth/issue-token"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.post(
                issue_token_url,
                json={"client_slug": ctx.client.slug, "api_key": activation_key},
            )
            response.raise_for_status()
            payload = response.json()
        token = str(payload.get("token") or "").strip()
        if not token:
            raise ValueError("Cloud sync token was not returned by the Railway backend.")
    except Exception as exc:
        status_payload = await _cloud_sync_status_for_client(session, ctx.client)
        status_payload["detail"] = f"Could not connect cloud sync: {exc}"
        return CloudSyncStatusPayload(**status_payload)

    save_runtime_env_values(
        {
            "CONVEY_CLOUD_SYNC_URL": DEFAULT_CLOUD_SYNC_URL,
            "CONVEY_CLOUD_SYNC_TOKEN": token,
            "CONVEY_CLIENT_SLUG": ctx.client.slug,
        }
    )
    apply_local_settings_env()
    await _ensure_sync_worker_running(ctx.client.id)

    status_payload = await _cloud_sync_status_for_client(session, ctx.client)
    status_payload["detail"] = "Cloud sync connected."
    return CloudSyncStatusPayload(**status_payload)


@app.post("/api/app/update/check")
def post_update_check(
    body: UpdateCheckRequest,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings = load_local_settings(user_id=str(ctx.user.id))
    return check_for_updates(
        current_version=get_app_version(),
        update_repository=body.update_repository or settings.get("updateRepository"),
        include_prerelease=(
            body.include_prerelease
            if body.include_prerelease is not None
            else bool(settings.get("includePrereleaseUpdates"))
        ),
    )


@app.post("/api/app/update/download")
def post_update_download(
    body: UpdateInstallRequest,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings = load_local_settings(user_id=str(ctx.user.id))
    release = check_for_updates(
        current_version=get_app_version(),
        update_repository=body.update_repository or settings.get("updateRepository"),
        include_prerelease=(
            body.include_prerelease
            if body.include_prerelease is not None
            else bool(settings.get("includePrereleaseUpdates"))
        ),
    )
    if release.get("error"):
        raise HTTPException(status_code=400, detail=str(release["error"]))
    if not release.get("update_available"):
        raise HTTPException(status_code=400, detail="No newer update is currently available.")
    try:
        download = download_update_installer(release)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "release": release,
        "download": download,
    }


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

    _triconvey_import_debug(
        "create_run_start",
        file_count=len(files),
        filenames=[getattr(f, "filename", "") or "<unnamed>" for f in files],
    )

    runtime = ensure_runtime_dirs()
    run_uuid = _uuid.uuid4()
    target_dir = runtime.ui_runs_dir / str(run_uuid)
    uploads_dir = target_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    reference_uploads: list[Path] = []
    for upload in files:
        if not upload.filename:
            continue
        destination = uploads_dir / _safe_filename(upload.filename)
        with destination.open("wb") as output_stream:
            shutil.copyfileobj(upload.file, output_stream)
        if destination.suffix.lower() == ".pdf":
            saved_paths.append(destination)
        elif _looks_like_triconvey_reference(destination):
            reference_uploads.append(destination)
        await upload.close()

    _triconvey_import_debug(
        "create_run_saved_uploads",
        pdf_count=len(saved_paths),
        reference_count=len(reference_uploads),
        reference_names=[p.name for p in reference_uploads],
    )

    deduped_reference_uploads = _dedupe_paths(reference_uploads)
    if len(deduped_reference_uploads) != len(reference_uploads):
        _triconvey_import_debug(
            "create_run_deduped_references",
            original_count=len(reference_uploads),
            deduped_count=len(deduped_reference_uploads),
        )

    resolved_reference_pdfs: list[Path] = []
    try:
        for reference_path in deduped_reference_uploads:
            resolved_reference_pdfs.extend(_resolve_triconvey_reference_upload(reference_path))
    except Exception as exc:
        _triconvey_import_debug("reference_resolution_exception", error=exc)
        raise HTTPException(
            status_code=400,
            detail=f"TriConvey import failed while resolving cached files: {exc}",
        ) from exc
    for resolved_pdf in _dedupe_paths(resolved_reference_pdfs):
        copied = _copy_into_uploads(resolved_pdf, uploads_dir)
        if copied not in saved_paths:
            saved_paths.append(copied)

    if not saved_paths:
        if reference_uploads:
            raise HTTPException(
                status_code=400,
                detail=(
                    "TriConvey drop detected, but the PDFs haven't been downloaded yet. "
                    "Open each document in TriConvey first so Smokeball caches it locally, "
                    "then drag the files again."
                ),
            )
        raise HTTPException(status_code=400, detail="No valid files were uploaded.")

    saved_settings = load_local_settings(user_id=str(ctx.user.id))
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

    convey_running = check_triconvey_running_passive()
    convey_launch_attempted = False
    convey_launch_ok: bool | None = None
    if resolved_triconvey_exe and not convey_running:
        convey_launch_attempted = True
        try:
            convey_launch_ok = ensure_local_convey_running(triconvey_exe=resolved_triconvey_exe)
            convey_running = convey_running or bool(convey_launch_ok)
            _triconvey_import_debug(
                "create_run_launch_triconvey",
                triconvey_exe=resolved_triconvey_exe,
                launched=convey_launch_ok,
            )
        except Exception as exc:
            convey_launch_ok = False
            _triconvey_import_debug(
                "create_run_launch_triconvey_error",
                triconvey_exe=resolved_triconvey_exe,
                error=exc,
            )

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
        LOG.info("Pipeline complete for run %s; starting DB persistence", run_uuid)
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
    LOG.info("DB persistence complete for run %s", run_uuid)
    warm_started = warm_brain_f_assets_async(target_dir)
    if warm_started:
        LOG.info("Brain F background warmup started for run %s", run_uuid)

    payload["convey_launch_attempted"] = convey_launch_attempted
    payload["convey_launch_ok"] = convey_launch_ok
    payload["convey_running"] = convey_running
    payload["brain_f_warming"] = warm_started
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
async def chat_about_run(
    run_id: str,
    body: ChatRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Ask a question about a run.

    Brain F (Anthropic agentic) is used when ANTHROPIC_API_KEY is set.
    Falls back to OpenAI token-ranked retrieval otherwise.

    Returns:
        answer            — prose answer
        citations         — [{file, page, quote}, ...]
        proposed_patches  — [{question_id, new_value, reason, status}, ...] (Brain F only)
        tool_calls_made   — int (Brain F only)
        confidence_note   — str | null (Brain F only)
    """
    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    try:
        saved_settings = load_local_settings(user_id=str(ctx.user.id))
        history = [{"role": m.role, "content": m.content} for m in (body.history or [])]
        model = body.model or saved_settings.get("defaultModelName", "gpt-4.1-mini")
        return ask_run_question(
            run_dir,
            question=body.question,
            model=model,
            history=history,
            ai_provider=saved_settings.get("aiProvider", "openai"),
            ai_mode=body.aiMode or saved_settings.get("aiMode", "cost_efficient"),
            mode=body.mode or "standard",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - surfaced to UI
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/chat-files")
async def upload_chat_files(
    run_id: str,
    files: list[UploadFile] = File(...),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    uploads_dir = run_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    saved_files: list[str] = []

    for upload in files:
        if not upload.filename:
            continue
        destination = uploads_dir / _safe_filename(upload.filename)
        with destination.open("wb") as output_stream:
            shutil.copyfileobj(upload.file, output_stream)
        await upload.close()
        if destination.suffix.lower() == ".pdf":
            saved_files.append(destination.name)
            try:
                document = load_pdf_document(destination)
                prime_cached_pdf_analysis(destination, document)
            except Exception:
                pass

    if not saved_files:
        raise HTTPException(status_code=400, detail="No valid PDF files were uploaded.")

    for path in (
        run_dir / "document_corpus_manifest.json",
        run_dir / "document_memory.json",
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    warm_brain_f_assets_async(run_dir)

    return {"uploaded": saved_files, "message": f"Uploaded {len(saved_files)} file(s) to Brain F."}


@app.post("/api/runs/{run_id}/apply-patches")
def apply_answer_patches(run_id: str, body: ApplyPatchRequest) -> dict[str, Any]:
    """Apply one or more proposed answer patches (from Brain F) to the run.

    The UI collects proposed_patches from a chat response, the conveyancer
    reviews them, and submits the ones they want applied here.
    """
    run_dir = _resolve_run_dir(run_id)
    try:
        updates: dict[str, dict[str, Any]] = {}
        unresolved: list[str] = []
        for patch in body.patches:
            targets = _resolve_patch_question_ids(patch.question_id)
            if not targets:
                unresolved.append(patch.question_id)
                continue
            for qid in targets:
                updates[qid] = {"value": patch.new_value, "needs_review": False}
        if unresolved:
            raise ValueError(
                "Could not resolve patch target(s): " + ", ".join(unresolved)
            )
        return save_review_answers(run_dir, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
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
    saved_settings = load_local_settings(user_id=str(ctx.user.id))

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
        _autofill_job_context[job_id_str] = {
            "run_dir": run_dir,
            "body": body.model_dump(mode="json"),
            "user_id": str(ctx.user.id),
        }

    worker = threading.Thread(
        target=_run_autofill_job,
        args=(job_id_str, job.id, run_dir, body, cancel_event, False, str(ctx.user.id)),
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
                "manual_action": mem.manual_action if mem else None,
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
        record.status = "cancelled"
        record.completed_at = _utc_now()
        record.error = "Autofill cancelled by user."
        _autofill_jobs[job_id] = record
    _force_stop_triconvey()
    return record.model_dump(mode="json")


@app.post("/api/autofill-jobs/{job_id}/continue")
async def continue_autofill_job(
    job_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    with _autofill_lock:
        record = _autofill_jobs.get(job_id)
        cancel_event = _autofill_cancel_events.get(job_id)
        context = _autofill_job_context.get(job_id)
        if record is None or cancel_event is None or context is None:
            raise HTTPException(status_code=404, detail=f"Autofill job '{job_id}' was not found.")
        if record.status != "awaiting_user":
            return record.model_dump(mode="json")
        record.status = "queued"
        record.error = None
        record.manual_action = None
        record.started_at = None
        record.completed_at = None
        _autofill_jobs[job_id] = record

    worker = threading.Thread(
        target=_run_autofill_job,
        args=(
            job_id,
            None,
            context["run_dir"],
            AutofillRequest.model_validate(context["body"]),
            cancel_event,
            True,
            context.get("user_id"),
        ),
        daemon=True,
    )
    worker.start()
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


def _looks_like_triconvey_reference(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.endswith(".smokeball.tmp")
        or name.endswith(".smokeball.json")
        or name == "triconvey-drop.json"
        or (name.startswith("triconvey-drop-") and name.endswith(".json"))
    )


def _copy_into_uploads(source: Path, uploads_dir: Path) -> Path:
    candidate = uploads_dir / _safe_filename(source.name)
    if candidate.exists():
        stem = candidate.stem
        suffix = candidate.suffix
        index = 2
        while candidate.exists():
            candidate = uploads_dir / f"{stem} ({index}){suffix}"
            index += 1
    shutil.copy2(source, candidate)
    return candidate


def _resolve_triconvey_reference_upload(reference_path: Path) -> list[Path]:
    _triconvey_import_debug("reference_upload", path=reference_path, size=reference_path.stat().st_size if reference_path.exists() else -1)
    payload = _read_triconvey_reference_payload(reference_path)
    if not payload:
        _triconvey_import_debug("reference_upload_unparsed", path=reference_path)
        return []
    return _resolve_triconvey_reference_payload(payload)


def _read_triconvey_reference_payload(reference_path: Path) -> dict[str, Any] | None:
    try:
        raw_text = reference_path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
    except Exception as exc:
        try:
            preview = reference_path.read_text(encoding="utf-8", errors="ignore")[:300]
        except Exception:
            preview = "<unreadable>"
        _triconvey_import_debug("reference_payload_parse_error", path=reference_path, error=exc, preview=preview)
        return None
    if not isinstance(payload, dict):
        _triconvey_import_debug("reference_payload_not_dict", path=reference_path, payload_type=type(payload).__name__)
        return None
    if isinstance(payload.get("LocalPaths"), list):
        _triconvey_import_debug("reference_payload_local_paths", count=len(payload.get("LocalPaths", [])))
        return payload
    if "MatterId" not in payload:
        _triconvey_import_debug("reference_payload_missing_matter", keys=list(payload.keys()))
        return None
    if not isinstance(payload.get("Files", []), list):
        _triconvey_import_debug("reference_payload_bad_files", files_type=type(payload.get("Files")).__name__)
        return None
    if not isinstance(payload.get("Folders", []), list):
        _triconvey_import_debug("reference_payload_bad_folders", folders_type=type(payload.get("Folders")).__name__)
        return None
    _triconvey_import_debug(
        "reference_payload_matter",
        matter_id=payload.get("MatterId", ""),
        file_count=len(payload.get("Files", [])),
        folder_count=len(payload.get("Folders", [])),
        file_ids=payload.get("Files", []),
    )
    return payload


def _resolve_triconvey_reference_payload(payload: dict[str, Any]) -> list[Path]:
    local_path_hits = _resolve_explicit_triconvey_paths(payload)
    if local_path_hits:
        _triconvey_import_debug("resolve_payload_local_hits", count=len(local_path_hits), names=[p.name for p in local_path_hits])
        return local_path_hits

    # TriConvey/Smokeball opens documents in an embedded WebView2 browser which
    # downloads PDFs to:
    #   %LOCALAPPDATA%\Temp\{hex6-9}\{hex6-9}\{hex6-9}\{filename}.pdf
    # The GUID in the drop payload cannot be derived into these path segments —
    # they are random WebView2 cache hashes.  We find the files by recency.
    #
    # Secondary fallback: the Smokeball desktop app caches at
    #   %LOCALAPPDATA%\Temp\Smokeball\{yyyy_mm_dd}\{guid_prefix8}\{filename}.pdf
    import re as _re

    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    temp_root = local_app_data / "Temp"
    matter_id = payload.get("MatterId", "")
    requested_file_count = max(0, len(payload.get("Files", [])))
    requested_folder_count = max(0, len(payload.get("Folders", [])))
    requested_document_count = max(1, requested_file_count + requested_folder_count)

    lookback_minutes = int(os.getenv("CONVEY_TRICONVEY_IMPORT_LOOKBACK_MINUTES", "60"))
    cutoff = datetime.now(UTC) - timedelta(minutes=max(5, lookback_minutes))

    def _limit_candidates(paths: list[Path]) -> list[Path]:
        ranked: list[tuple[datetime, Path]] = []
        for path in paths:
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            ranked.append((mtime, path))
        ranked.sort(key=lambda item: (item[0], item[1].name.lower()), reverse=True)
        limited = [path for _, path in ranked[:requested_document_count]]
        _triconvey_import_debug(
            "resolve_payload_limited",
            matter_id=matter_id,
            requested_document_count=requested_document_count,
            candidate_count=len(paths),
            selected_names=[path.name for path in limited],
        )
        return limited

    # --- Primary: %TEMP%\{hex}\{hex}\{hex}\*.pdf (WebView2 download cache) ----
    _hex_dir = _re.compile(r"^[0-9a-f]{5,9}$", _re.I)

    def _collect_hex3_pdfs(root: Path) -> list[Path]:
        seen: set[Path] = set()
        found: list[Path] = []
        try:
            l1_dirs = [d for d in root.iterdir() if d.is_dir() and _hex_dir.match(d.name)]
        except OSError:
            return found
        for l1 in l1_dirs:
            try:
                l2_dirs = [d for d in l1.iterdir() if d.is_dir() and _hex_dir.match(d.name)]
            except OSError:
                continue
            for l2 in l2_dirs:
                try:
                    l3_dirs = [d for d in l2.iterdir() if d.is_dir() and _hex_dir.match(d.name)]
                except OSError:
                    continue
                for l3 in l3_dirs:
                    try:
                        pdfs = list(l3.glob("*.pdf"))
                    except OSError:
                        continue
                    for pdf in pdfs:
                        if pdf in seen:
                            continue
                        try:
                            mtime = datetime.fromtimestamp(pdf.stat().st_mtime, tz=UTC)
                        except OSError:
                            continue
                        if mtime >= cutoff:
                            seen.add(pdf)
                            found.append(pdf)
        return found

    webview2_pdfs = _collect_hex3_pdfs(temp_root)
    if webview2_pdfs:
        result = _limit_candidates(webview2_pdfs)
        _triconvey_import_debug("resolve_payload_webview2_hits", matter_id=matter_id, count=len(result), names=[p.name for p in result])
        LOG.info(
            "Resolved %d PDF(s) from WebView2 temp cache for matter %s: %s",
            len(result), matter_id, [p.name for p in result],
        )
        return result

    # --- Fallback: %TEMP%\Smokeball\{date}\{guid_prefix8}\*.pdf ---------------
    smokeball_root = temp_root / "Smokeball"
    if not smokeball_root.exists():
        LOG.warning(
            "No PDFs found for matter %s — open each file in TriConvey first "
            "so Smokeball downloads it locally, then drag the files again.",
            matter_id,
        )
        return []

    fallback: list[Path] = []
    try:
        date_dirs = sorted(
            (d for d in smokeball_root.iterdir() if d.is_dir() and d.name != "webview2"),
            reverse=True,
        )
    except OSError:
        return []

    for date_dir in date_dirs:
        try:
            sub_dirs = [d for d in date_dir.iterdir() if d.is_dir()]
        except OSError:
            continue
        for sub_dir in sub_dirs:
            try:
                pdfs = list(sub_dir.glob("*.pdf"))
            except OSError:
                continue
            for pdf in pdfs:
                try:
                    mtime = datetime.fromtimestamp(pdf.stat().st_mtime, tz=UTC)
                except OSError:
                    continue
                if mtime >= cutoff:
                    fallback.append(pdf)

    if fallback:
        result = _limit_candidates(fallback)
        _triconvey_import_debug("resolve_payload_smokeball_hits", matter_id=matter_id, count=len(result), names=[p.name for p in result])
        LOG.info(
            "Resolved %d PDF(s) from Smokeball app cache for matter %s: %s",
            len(result), matter_id, [p.name for p in result],
        )
        return result

    LOG.warning(
        "No cached PDFs found for matter %s — open each file in TriConvey first.",
        matter_id,
    )
    _triconvey_import_debug("resolve_payload_no_hits", matter_id=matter_id)
    return []


def _resolve_explicit_triconvey_paths(payload: dict[str, Any]) -> list[Path]:
    explicit_paths = payload.get("LocalPaths")
    if not isinstance(explicit_paths, list):
        return []

    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw_value in explicit_paths:
        path = _normalize_triconvey_local_path(str(raw_value or ""))
        _triconvey_import_debug("explicit_path_candidate", raw=raw_value, normalized=path or "<none>")
        if path is None or path in seen or not path.exists():
            continue
        seen.add(path)
        if path.suffix.lower() == ".pdf":
            resolved.append(path)
            continue
        if _looks_like_triconvey_reference(path):
            resolved.extend(_resolve_triconvey_reference_upload(path))
    if resolved:
        _triconvey_import_debug("explicit_path_hits", count=len(resolved), names=[p.name for p in resolved])
        LOG.info("Resolved %d PDF(s) from explicit TriConvey local paths: %s", len(resolved), [p.name for p in resolved])
    return _dedupe_paths(resolved)


def _normalize_triconvey_local_path(raw_value: str) -> Path | None:
    from urllib.parse import unquote, urlparse

    text = raw_value.strip().strip('"').strip("'")
    if not text:
        return None
    if text.startswith("@"):
        text = text[1:]
    if text.lower().startswith("file:///"):
        parsed = urlparse(text)
        path_text = unquote(parsed.path or "")
        if path_text.startswith("/") and len(path_text) >= 3 and path_text[2] == ":":
            path_text = path_text[1:]
        return Path(path_text)
    if len(text) >= 3 and text[1] == ":":
        return Path(text)
    return None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _resolve_patch_question_ids(requested_id: str) -> list[str]:
    registry = load_question_registry()
    if requested_id in registry:
        return [requested_id]

    # Primary: match by fact_path (AI uses fact paths like 'rates.water.authority_name')
    by_fact_path: list[str] = []
    for qid, question in registry.items():
        fact_paths = question.fact_paths or []
        if requested_id in fact_paths:
            by_fact_path.append(qid)
    if by_fact_path:
        return by_fact_path

    # Fallback: fuzzy match against question label (normalise both sides)
    def _norm(s: str) -> str:
        import re as _re
        return _re.sub(r"[^a-z0-9]", "", (s or "").lower())

    norm_req = _norm(requested_id)
    if len(norm_req) >= 4:
        for qid, question in registry.items():
            label = getattr(question, "label", None) or ""
            if norm_req in _norm(label) or _norm(label) in norm_req:
                return [qid]

    return []


def _run_autofill_job(
    job_id: str,
    db_job_id: "uuid.UUID | None",
    run_dir: Path,
    body: AutofillRequest,
    cancel_event: threading.Event,
    resume_from_property_details: bool = False,
    user_id: str | None = None,
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
        if db_job_id is None:
            return
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
        set_autofill_activity(run_dir, True)
        saved_settings = load_local_settings(user_id=user_id)
        result = autofill_run(
            run_dir,
            dry_run=body.dry_run,
            triconvey_exe=body.triconvey_exe or saved_settings["triconveyPath"] or None,
            skip_review_gate=body.skip_review_gate,
            cancel_requested=cancel_event.is_set,
            resume_from_property_details=resume_from_property_details,
            preferred_autofill_fields=saved_settings.get("preferredAutofillFields") or [],
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
        from triconvey_agent.canonical.brain_e.executor import ManualInterventionRequired
        if isinstance(exc, ManualInterventionRequired):
            _db_update("awaiting_user", error=exc.message)
            with _autofill_lock:
                record = _autofill_jobs[job_id]
                record.status = "awaiting_user"
                record.error = exc.message
                record.manual_action = {"action": exc.action, "message": exc.message, "cta": "Continue"}
                _autofill_jobs[job_id] = record
            return
        final_status = "cancelled" if cancel_event.is_set() else "failed"
        _db_update(final_status, completed=True, error=str(exc))
        with _autofill_lock:
            record = _autofill_jobs[job_id]
            record.status = final_status
            record.error = str(exc)
            record.completed_at = _utc_now()
            _autofill_jobs[job_id] = record
    finally:
        set_autofill_activity(run_dir, False)


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
