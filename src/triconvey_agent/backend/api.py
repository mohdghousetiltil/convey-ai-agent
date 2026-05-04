from __future__ import annotations

# Load .env variables BEFORE any imports that depend on environment variables
from dotenv import load_dotenv
load_dotenv()

import asyncio
import logging
from functools import partial
import base64
import json
import os
import re
import subprocess
import shutil
import threading
import tempfile
import time
import zipfile
import uuid as _uuid
from datetime import datetime, timedelta, UTC
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4
import httpx

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

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
from triconvey_agent.db.bootstrap import apply_runtime_migrations
from triconvey_agent.copy_rules import find_best_copy_rule_match
from triconvey_agent.backend.triconvey_import_utils import (
    build_priority_outgoing_rows,
    parse_amount_text,
    wait_for_triconvey_paths,
)
from triconvey_agent.backend.service import (
    ask_run_question,
    autofill_run,
    build_review_run,
    check_triconvey_running_passive,
    confirm_corpus_document,
    ensure_local_convey_running,
    extract_corpus_entry_async,
    get_corpus_state,
    load_run_payload,
    save_review_answers,
    set_autofill_activity,
    warm_brain_f_assets_async,
    process_chat_document,
    apply_chat_document_changes,
)
from triconvey_agent.brain_f.cache import prime_cached_pdf_analysis
from triconvey_agent.ingest.pdf_loader import load_pdf_document
from triconvey_agent.canonical.questions.loader import load_question_registry
from triconvey_agent.db.repositories import (
    AnswerRepo,
    ClientRepo,
    CopyRuleRepo,
    MatterRepo,
    RunRepo,
    SyncQueueRepo,
)
from triconvey_agent.db.session import dispose_engine, ensure_runtime_schema, get_session
from triconvey_agent.sync.worker import SyncWorker, start_sync_worker

LOG = logging.getLogger(__name__)


class ReviewAnswerUpdate(BaseModel):
    value: Any = None
    needs_review: bool = False


class SaveAnswersRequest(BaseModel):
    updates: dict[str, ReviewAnswerUpdate] = Field(default_factory=dict)


class CopyRuleCreateRequest(BaseModel):
    rule_type: str = "water_authority"
    authority_name: str = Field(..., min_length=1)
    annual_amount: float = Field(..., ge=0)
    notes: str | None = None
    is_active: bool = True


class CopyRuleUpdateRequest(BaseModel):
    authority_name: str = Field(..., min_length=1)
    annual_amount: float = Field(..., ge=0)
    notes: str | None = None
    is_active: bool = True


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


class AutofillActivityEvent(BaseModel):
    index: int
    ts: str | None = None
    kind: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AutofillActivityPayload(BaseModel):
    job_id: str
    status: str
    cursor: int
    events: list[AutofillActivityEvent] = Field(default_factory=list)
    latest_screenshot_url: str | None = None
    latest_screenshot_name: str | None = None


class CreateRunResult(BaseModel):
    run: dict[str, Any]
    convey_launch_attempted: bool = False
    convey_launch_ok: bool | None = None


class RecentRunSummary(BaseModel):
    run_id: str
    status: str
    created_at: str | None = None
    completed_at: str | None = None
    time_taken_seconds: float | None = None
    client_name: str = ""
    volume_folio: str = ""
    property_address: str = ""
    matter_id: str | None = None
    matter_ref: str | None = None
    summary_text: str | None = None
    local_only: bool = False


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    question: str
    model: str | None = None          # if None, uses saved defaultModelName
    history: list[ChatMessage] = Field(default_factory=list)
    mode: str = "standard"            # "quick" | "standard" | "thorough"
    session_id: str | None = None     # Feature 4: vector memory session scoping
    aiMode: str | None = None         # e.g. "cost_efficient" | "quality"


class AnswerPatch(BaseModel):
    question_id: str
    new_value: str
    reason: str


class ApplyPatchRequest(BaseModel):
    patches: list[AnswerPatch]

class ResolveTriconveyReferenceRequest(BaseModel):
    payload_text: str = ""


class LocalSettingsPayload(BaseModel):
    language: str = "English"
    openAiApiKey: str = ""
    anthropicApiKey: str = ""
    googleApiKey: str = ""              # Feature 1: Google Gemini
    aiProvider: str = "openai"          # "openai" | "anthropic" | "google" | "hybrid"
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


async def _nudge_sync_worker() -> None:
    worker = _sync_worker_ref.get("worker")
    if worker is None:
        return
    try:
        await worker.nudge()
    except Exception:
        LOG.debug("Could not nudge sync worker for immediate drain.", exc_info=True)


async def _fetch_water_copy_rules(
    session: AsyncSession,
    *,
    client_id: _uuid.UUID,
) -> list[tuple[str, float]]:
    """Return (authority_name, annual_amount) pairs for active water copy rules."""
    try:
        rules = await CopyRuleRepo.list_for_client(
            session,
            client_id=client_id,
            rule_type="water_authority",
            include_inactive=False,
        )
        return [(r.authority_name, float(r.annual_amount)) for r in rules]
    except Exception:
        return []


async def _apply_copy_rule_fallbacks_to_run(
    session: AsyncSession,
    *,
    client_id: _uuid.UUID,
    run_dir: Path,
) -> None:
    rules = await CopyRuleRepo.list_for_client(
        session,
        client_id=client_id,
        rule_type="water_authority",
        include_inactive=False,
    )

    answers_path = run_dir / "answers.json"
    facts_path = run_dir / "facts.json"
    if not answers_path.exists() or not facts_path.exists():
        return

    try:
        answers = json.loads(answers_path.read_text(encoding="utf-8"))
        facts_raw = json.loads(facts_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(answers, dict):
        return

    facts_by_path = facts_raw.get("facts", {}) if isinstance(facts_raw, dict) else {}
    build_priority_outgoing_rows(answers, facts_by_path, rules)
    if not rules:
        answers_path.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")
        return

    water_authority_facts = list(facts_by_path.get("rates.water.authority_name") or [])
    vendor_authority_facts = [
        fact for fact in water_authority_facts if "vendor_form" in str(fact.get("extractor") or "")
    ]
    authority_candidates = water_authority_facts + vendor_authority_facts
    authority_name = None
    for fact in authority_candidates:
        value = str(fact.get("value") or "").strip()
        if value:
            authority_name = value
            break
    if not authority_name:
        answers_path.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")
        return

    match = find_best_copy_rule_match(
        authority_name,
        [(row.authority_name, row.annual_amount) for row in rules],
    )
    if match is None:
        answers_path.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")
        return

    # Find which row slot currently holds this water authority (dynamic — not hardcoded to row 2)
    water_row_num: int | None = None
    for row_num in range(1, 5):
        auth_answer = answers.get(f"sec32_1.1_outgoing_{row_num}_authority") or {}
        auth_val = str(auth_answer.get("human_value_json") or auth_answer.get("value_json") or "").strip()
        if auth_val.lower() == authority_name.lower():
            water_row_num = row_num
            break
    if water_row_num is None:
        answers_path.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")
        return

    amount_id = f"sec32_1.1_outgoing_{water_row_num}_amount"
    current_answer = answers.get(amount_id) or {}
    # Only skip if a human has already manually edited this value
    if current_answer.get("human_edited"):
        answers_path.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")
        return

    formatted_amount = f"${match.annual_amount:,.2f}"
    next_hints = dict(current_answer.get("presentation_hints") or {})
    next_hints["copy_rule_fallback"] = {
        "matched_authority_name": match.authority_name,
        "source_authority_name": authority_name,
        "match_score": round(match.score, 4),
        "matched_on": match.matched_on,
    }
    current_answer["value_json"] = formatted_amount
    current_answer["presentation_hints"] = next_hints
    current_answer["confidence"] = max(float(current_answer.get("confidence") or 0.0), 0.86)
    current_answer["needs_review"] = False
    current_answer["review_reasons"] = []
    answers[amount_id] = current_answer
    answers_path.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")


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
    try:
        await apply_runtime_migrations()
    except Exception as exc:
        LOG.exception("Failed to apply runtime migrations: %s", exc)
    try:
        import sys as _sys
        from triconvey_agent.backend.settings import load_local_settings as _load_local_settings

        auto_launch = os.getenv("CONVEY_AUTO_LAUNCH_TRICONVEY_ON_START", "").strip().lower()
        # Desktop default: do NOT auto-open TriConvey on startup unless explicitly enabled.
        should_auto_launch = auto_launch in {"1", "true", "yes", "on"}
        if should_auto_launch:
            settings = _load_local_settings()
            triconvey_exe = str(settings.get("triconveyPath") or "").strip() or None
            if triconvey_exe and not check_triconvey_running_passive():
                def _launch() -> None:
                    try:
                        ok = ensure_local_convey_running(triconvey_exe=triconvey_exe)
                        _triconvey_import_debug("startup_launch_triconvey", triconvey_exe=triconvey_exe, launched=ok)
                    except Exception as exc:  # pragma: no cover
                        _triconvey_import_debug("startup_launch_triconvey_error", triconvey_exe=triconvey_exe, error=exc)

                threading.Thread(target=_launch, daemon=True).start()
    except Exception:
        pass

    client_slug = os.getenv("CONVEY_CLIENT_SLUG", "").strip()
    if client_slug:
        # Resolve the local client_id for the sync worker.
        from triconvey_agent.db.session import get_session_factory

        await ensure_runtime_schema()
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
if _ui_dist_dir.exists() and (_ui_dist_dir / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_ui_dist_dir / "assets")), name="ui-assets")
_public_dir = get_runtime_paths().bundle_root / "public"
if _public_dir.exists():
    app.mount("/public", StaticFiles(directory=str(_public_dir)), name="public-assets")


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


def _serialize_copy_rule(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "rule_type": row.rule_type,
        "authority_name": row.authority_name,
        "annual_amount": float(row.annual_amount),
        "notes": row.notes,
        "is_active": bool(row.is_active),
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@app.get("/api/copy-rules")
async def list_copy_rules(
    rule_type: str = "water_authority",
    include_inactive: bool = False,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = await CopyRuleRepo.list_for_client(
        session,
        client_id=ctx.client.id,
        rule_type=rule_type,
        include_inactive=include_inactive,
    )
    return [_serialize_copy_rule(row) for row in rows]


@app.post("/api/copy-rules", status_code=201)
async def create_copy_rule(
    body: CopyRuleCreateRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await CopyRuleRepo.create(
        session,
        client_id=ctx.client.id,
        rule_type=body.rule_type,
        authority_name=body.authority_name,
        annual_amount=body.annual_amount,
        notes=body.notes,
        is_active=body.is_active,
        changed_by=ctx.user.email,
    )
    await _nudge_sync_worker()
    return _serialize_copy_rule(row)


@app.put("/api/copy-rules/{rule_id}")
async def update_copy_rule(
    rule_id: str,
    body: CopyRuleUpdateRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        rule_uuid = _uuid.UUID(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid copy rule id.") from exc

    row = await CopyRuleRepo.update(
        session,
        client_id=ctx.client.id,
        rule_id=rule_uuid,
        authority_name=body.authority_name,
        annual_amount=body.annual_amount,
        notes=body.notes,
        is_active=body.is_active,
        changed_by=ctx.user.email,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Copy rule not found.")
    await _nudge_sync_worker()
    return _serialize_copy_rule(row)


@app.delete("/api/copy-rules/{rule_id}")
async def delete_copy_rule(
    rule_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        rule_uuid = _uuid.UUID(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid copy rule id.") from exc

    row = await CopyRuleRepo.soft_delete(
        session,
        client_id=ctx.client.id,
        rule_id=rule_uuid,
        changed_by=ctx.user.email,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Copy rule not found.")
    await _nudge_sync_worker()
    return {"ok": True, "id": rule_id}


@app.post("/api/runs/{run_id}/authority-rows/{row_num}/use-copy-rule")
async def use_copy_rule_for_authority_row(
    run_id: str,
    row_num: int,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if row_num not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="row_num must be 1–4.")

    run_dir = _resolve_run_dir(run_id)
    answers_path = run_dir / "answers.json"
    if not answers_path.exists():
        raise HTTPException(status_code=404, detail="Run not found.")

    try:
        answers = json.loads(answers_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read answers.") from exc

    authority_id = f"sec32_1.1_outgoing_{row_num}_authority"
    amount_id = f"sec32_1.1_outgoing_{row_num}_amount"

    auth_answer = answers.get(authority_id) or {}
    authority_name = str(
        auth_answer.get("human_value_json") or auth_answer.get("value_json") or ""
    ).strip()
    if not authority_name:
        raise HTTPException(status_code=404, detail="No copy price found for this authority.")

    rules = await CopyRuleRepo.list_for_client(
        session,
        client_id=ctx.client.id,
        rule_type="water_authority",
        include_inactive=False,
    )
    if not rules:
        raise HTTPException(status_code=404, detail="No copy price found for this authority.")

    match = find_best_copy_rule_match(
        authority_name,
        [(row.authority_name, row.annual_amount) for row in rules],
    )
    if match is None:
        raise HTTPException(status_code=404, detail="No copy price found for this authority.")

    formatted_amount = f"${match.annual_amount:,.2f}"
    current_answer = answers.get(amount_id) or {}
    original_value = current_answer.get("human_value_json") or current_answer.get("value_json")
    original_source = current_answer.get("source")

    next_hints = dict(current_answer.get("presentation_hints") or {})
    next_hints["copy_rule_manual_override"] = {
        "original_value": original_value,
        "original_source": original_source,
        "matched_authority_name": match.authority_name,
        "source_authority_name": authority_name,
        "match_score": round(match.score, 4),
        "matched_on": match.matched_on,
        "overridden_by": ctx.user.email,
    }

    current_answer["value_json"] = formatted_amount
    current_answer["presentation_hints"] = next_hints
    current_answer["confidence"] = 1.0
    current_answer["needs_review"] = False
    current_answer["review_reasons"] = []
    current_answer["source"] = "copy_rule_manual_override"
    answers[amount_id] = current_answer

    answers_path.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")
    await _nudge_sync_worker()

    return {
        "ok": True,
        "row_num": row_num,
        "authority_name": authority_name,
        "matched_rule": match.authority_name,
        "amount": formatted_amount,
        "match_score": round(match.score, 4),
    }


# ---------------------------------------------------------------------------
# Feature 2: Vector memory endpoints
# ---------------------------------------------------------------------------


class MemorySaveRequest(BaseModel):
    session_id: str
    content: str
    embedding: list[float]


class MemorySearchRequest(BaseModel):
    query_embedding: list[float]
    limit: int = 5


@app.post("/api/memory")
async def save_memory_endpoint(
    body: MemorySaveRequest,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Persist a memory record to the vector store."""
    try:
        from triconvey_agent.memory.vector_store import save_memory as _save_memory
        ok = await _save_memory(
            user_id=str(ctx.user.id),
            session_id=body.session_id,
            content=body.content,
            embedding=body.embedding,
        )
        return {"saved": ok}
    except Exception as exc:
        LOG.warning("/api/memory POST failed: %s", exc)
        return {"saved": False, "error": str(exc)}


@app.post("/api/memory/search")
async def search_memory_endpoint(
    body: MemorySearchRequest,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Search vector memory by embedding similarity."""
    try:
        from triconvey_agent.memory.vector_store import search_memory as _search_memory
        results = await _search_memory(
            user_id=str(ctx.user.id),
            query_embedding=body.query_embedding,
            limit=min(body.limit, 20),
        )
        return {"results": results, "count": len(results)}
    except Exception as exc:
        LOG.warning("/api/memory/search POST failed: %s", exc)
        return {"results": [], "count": 0, "error": str(exc)}


@app.delete("/api/memory/expired")
async def cleanup_expired_memory(
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Hard-delete all memory rows past their 20-day TTL."""
    try:
        from triconvey_agent.memory.vector_store import cleanup_expired as _cleanup_expired
        deleted = await _cleanup_expired()
        return {"deleted": deleted}
    except Exception as exc:
        LOG.warning("/api/memory/expired DELETE failed: %s", exc)
        return {"deleted": 0, "error": str(exc)}


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
async def create_run(background_tasks: BackgroundTasks, 
    files: list[UploadFile] = File(default=[]),
    use_ai_review: bool = Form(False),
    model: str = Form("gpt-4.1-mini"),
    triconvey_exe: str | None = Form(None),
    reanalyse_run_id: str | None = Form(None),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Upload documents, run extraction pipeline, persist results to DB + files."""
    import asyncio
    import uuid as _uuid
    from functools import partial

    # Re-analysis mode: reuse an existing run's uploaded documents.
    if reanalyse_run_id:
        runtime = ensure_runtime_dirs()
        source_dir = runtime.ui_runs_dir / reanalyse_run_id
        if not source_dir.exists():
            raise HTTPException(status_code=404, detail=f"Run {reanalyse_run_id} not found.")
        # Collect PDFs from uploads/ and root of source run dir.
        source_uploads = source_dir / "uploads"
        saved_paths: list[Path] = []
        for search_dir in (source_uploads, source_dir):
            if search_dir.exists():
                saved_paths.extend(
                    p for p in search_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in (".pdf", ".docx", ".doc")
                )
        if not saved_paths:
            raise HTTPException(
                status_code=400,
                detail=f"Run {reanalyse_run_id} has no cached documents to re-analyse.",
            )
        run_uuid = _uuid.uuid4()
        target_dir = runtime.ui_runs_dir / str(run_uuid)
        new_uploads_dir = target_dir / "uploads"
        new_uploads_dir.mkdir(parents=True, exist_ok=True)
        # Copy source PDFs into new run dir.
        copied_paths: list[Path] = []
        for p in saved_paths:
            dest = new_uploads_dir / p.name
            shutil.copy2(p, dest)
            copied_paths.append(dest)
        # Also copy any new files uploaded alongside the re-analysis request.
        if files:
            extra, _ = await _persist_uploaded_files(new_uploads_dir, files)
            copied_paths.extend(extra)
        saved_settings = load_local_settings(user_id=str(ctx.user.id))
        resolved_model = model or saved_settings["defaultModelName"]
        run_row = await RunRepo.create(
            session,
            client_id=ctx.client.id,
            run_id=run_uuid,
            user_id=ctx.user.id,
            model=resolved_model,
            use_ai_review=use_ai_review,
        )
        await session.commit()
        water_copy_rules = await _fetch_water_copy_rules(session, client_id=ctx.client.id)
        try:
            loop = asyncio.get_event_loop()
            pipeline_fn = partial(
                build_review_run,
                copied_paths,
                run_dir=target_dir,
                use_ai_review=use_ai_review,
                model=resolved_model,
                copy_rules=water_copy_rules,
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
        await _apply_copy_rule_fallbacks_to_run(session, client_id=ctx.client.id, run_dir=target_dir)
        payload = load_run_payload(target_dir)
        await _persist_run_to_db(session, payload, client_id=ctx.client.id, run_uuid=run_uuid)
        await _nudge_sync_worker()
        return payload

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

    try:
        saved_paths, reference_uploads = await _persist_uploaded_files(uploads_dir, files)
    except Exception as exc:
        _triconvey_import_debug("reference_resolution_exception", error=exc)
        raise HTTPException(
            status_code=400,
            detail=f"TriConvey import failed while resolving cached files: {exc}",
        ) from exc

    _triconvey_import_debug(
        "create_run_saved_uploads",
        pdf_count=len(saved_paths),
        reference_count=len(reference_uploads),
        reference_names=[p.name for p in reference_uploads],
    )

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
        user_id=ctx.user.id,
        model=resolved_model,
        use_ai_review=use_ai_review,
    )
    await session.commit()

    convey_launch_attempted = False
    convey_launch_ok: bool | None = None

    # Run the sync-heavy pipeline in a background task.
    # We create a wrapper that handles the executor and DB persistence.
    loop = asyncio.get_event_loop()
    background_tasks.add_task(
        _run_analysis_pipeline_background,
        loop=loop,
        client_id=ctx.client.id,
        run_uuid=run_uuid,
        saved_paths=saved_paths,
        target_dir=target_dir,
        use_ai_review=use_ai_review,
        resolved_model=resolved_model,
    )

    return {
        "run_id": str(run_uuid),
        "status": "pending",
        "progress_pct": 0.0,
        "progress_status": "Starting...",
    }


async def _run_analysis_pipeline_background(
    loop: asyncio.AbstractEventLoop,
    client_id: _uuid.UUID,
    run_uuid: _uuid.UUID,
    saved_paths: list[Path],
    target_dir: Path,
    use_ai_review: bool,
    resolved_model: str,
):
    """Background worker that orchestrates the analysis pipeline and DB updates."""
    from triconvey_agent.db.session import get_session_factory

    factory = get_session_factory()

    def progress_callback(pct: float, status: str):
        # Fire-and-forget DB update for progress.
        asyncio.run_coroutine_threadsafe(
            _update_run_progress_async(factory, client_id, run_uuid, pct, status),
            loop
        )

    # Fetch copy rules before entering the sync executor so Brain A can inject
    # the DB price into the fact store before Brain D runs.
    water_copy_rules: list[tuple[str, float]] = []
    try:
        async with factory() as _session:
            water_copy_rules = await _fetch_water_copy_rules(_session, client_id=client_id)
    except Exception:
        pass

    try:
        # 1) Execute the pipeline in the thread pool.
        pipeline_fn = partial(
            build_review_run,
            saved_paths,
            run_dir=target_dir,
            use_ai_review=use_ai_review,
            model=resolved_model,
            progress_callback=progress_callback,
            copy_rules=water_copy_rules,
        )
        payload: dict[str, Any] = await loop.run_in_executor(None, pipeline_fn)
        LOG.info("Pipeline complete for run %s; starting DB persistence", run_uuid)

        # 2) Persist to DB.
        async with factory() as session:
            await _apply_copy_rule_fallbacks_to_run(session, client_id=client_id, run_dir=target_dir)
            payload = load_run_payload(target_dir)
            await _persist_run_to_db(session, payload, client_id=client_id, run_uuid=run_uuid)
            await session.commit()

        LOG.info("DB persistence complete for run %s", run_uuid)
        await _nudge_sync_worker()
        warm_brain_f_assets_async(target_dir)

    except Exception as exc:
        LOG.exception("Background analysis pipeline failed for run %s", run_uuid)
        async with factory() as session:
            await session.rollback()
            await RunRepo.update_status(
                session,
                client_id=client_id,
                run_id=run_uuid,
                status="failed",
                error_message=str(exc),
            )
            await session.commit()


async def _update_run_progress_async(
    factory: Any,
    client_id: _uuid.UUID,
    run_uuid: _uuid.UUID,
    pct: float,
    status: str
):
    async with factory() as session:
        await RunRepo.update_status(
            session,
            client_id=client_id,
            run_id=run_uuid,
            status="running",
            progress_pct=pct,
            progress_status=status
        )
        await session.commit()


@app.get("/api/runs/recent", response_model=list[RecentRunSummary])
async def get_recent_runs(
    limit: int = 10,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[RecentRunSummary]:
    rows = await RunRepo.list_recent_with_matter(
        session,
        client_id=ctx.client.id,
        user_id=ctx.user.id,
        limit=max(1, min(limit, 25)),
    )

    summaries: list[RecentRunSummary] = []
    for run, matter in rows:
        client_name = ""
        volume_folio = matter.volume_folio if matter else ""
        property_address = matter.property_address if matter else ""

        try:
            payload = load_run_payload(_resolve_run_dir(str(run.id)))
            client_name = str(
                payload.get("matter", {}).get("client_name")
                or payload.get("client_name")
                or ""
            ).strip()
            if not volume_folio:
                volume_folio = str(payload.get("matter", {}).get("volume_folio") or "").strip()
            if not property_address:
                property_address = str(payload.get("matter", {}).get("property_address") or "").strip()
        except Exception:
            pass

        summaries.append(
            RecentRunSummary(
                run_id=str(run.id),
                status=run.status,
                created_at=run.created_at.isoformat() if run.created_at else None,
                completed_at=run.completed_at.isoformat() if run.completed_at else None,
                time_taken_seconds=(
                    max((run.completed_at - run.created_at).total_seconds(), 0.0)
                    if run.created_at and run.completed_at
                    else None
                ),
                client_name=client_name,
                volume_folio=volume_folio or "",
                property_address=property_address or "",
                matter_id=str(matter.id) if matter else None,
                matter_ref=matter.matter_ref if matter else None,
                summary_text=run.summary_text,
                local_only=bool(run.local_only),
            )
        )

    return summaries


@app.get("/api/runs/{run_id}")
async def get_run(
    run_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    import uuid as _uuid

    # Verify ownership via DB if run_id is a valid UUID.
    run_row = None
    try:
        run_uuid = _uuid.UUID(run_id)
        run_row = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
        if run_row is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        pass  # legacy string run_id — skip DB ownership check

    if run_row and run_row.status in {"pending", "running"}:
        return {
            "run_id": str(run_row.id),
            "status": run_row.status,
            "progress_pct": run_row.progress_pct or 0.0,
            "progress_status": run_row.progress_status or "Processing...",
            "error_message": run_row.error_message,
        }
    if run_row and run_row.status == "failed":
        return {
            "run_id": str(run_row.id),
            "status": run_row.status,
            "progress_pct": run_row.progress_pct or 0.0,
            "progress_status": run_row.progress_status or "Failed",
            "error_message": run_row.error_message,
        }

    run_dir = _resolve_run_dir(run_id)
    await _apply_copy_rule_fallbacks_to_run(session, client_id=ctx.client.id, run_dir=run_dir)
    payload = load_run_payload(run_dir)
    if run_row:
        payload["status"] = "completed" if run_row.status in {"complete", "completed"} else run_row.status
        payload["progress_pct"] = 100.0
        payload["progress_status"] = "Complete"
    return payload


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
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
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
        await _nudge_sync_worker()

    # 2) Mirror to file-based storage so the existing autofill pipeline keeps working.
    save_review_answers(run_dir, updates)
    await _apply_copy_rule_fallbacks_to_run(session, client_id=ctx.client.id, run_dir=run_dir)
    return load_run_payload(run_dir)


@app.post("/api/runs/{run_id}/chat")
async def chat_about_run(
    run_id: str,
    body: ChatRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Ask a question about a run.

    Brain F (Anthropic/OpenAI/Google agentic) answers using extracted facts,
    document corpus, and (Feature 4) vector memory context.

    Returns:
        answer            — prose answer
        citations         — [{file, page, quote}, ...]
        proposed_patches  — [{question_id, new_value, reason, status}, ...]
        tool_calls_made   — int
        confidence_note   — str | null
        session_id        — UUID for this chat session
    """
    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    try:
        saved_settings = load_local_settings(user_id=str(ctx.user.id))
        history = [{"role": m.role, "content": m.content} for m in (body.history or [])]
        model = body.model or saved_settings.get("defaultModelName", "gpt-4.1-mini")
        user_id = str(ctx.user.id)
        session_id = body.session_id or str(uuid4())

        # ── Feature 4: retrieve vector memory context ──────────────────
        vector_memories: list[dict] = []
        try:
            from triconvey_agent.ai.multi_client import MultiModelClient
            from triconvey_agent.memory.vector_store import search_memory as _search_memory

            mc = MultiModelClient(
                provider=saved_settings.get("aiProvider", "openai"),
                model=model,
            )
            query_embedding = mc.embed(body.question)
            vector_memories = await _search_memory(user_id, query_embedding, limit=5)
        except Exception as _mem_exc:
            LOG.debug("Vector memory retrieval skipped: %s", _mem_exc)

        result = ask_run_question(
            run_dir,
            question=body.question,
            model=model,
            history=history,
            ai_provider=saved_settings.get("aiProvider", "openai"),
            ai_mode=body.aiMode or saved_settings.get("aiMode", "cost_efficient"),
            mode=body.mode or "standard",
            session_id=session_id,
            vector_memories=vector_memories,
        )

        # ── Feature 4: save this exchange to vector memory ─────────────
        try:
            from triconvey_agent.ai.multi_client import MultiModelClient
            from triconvey_agent.memory.vector_store import save_memory as _save_memory

            exchange_text = f"Q: {body.question}\nA: {result.get('answer', '')}"
            mc2 = MultiModelClient(
                provider=saved_settings.get("aiProvider", "openai"),
                model=model,
            )
            exchange_embedding = mc2.embed(exchange_text)
            await _save_memory(user_id, session_id, exchange_text, exchange_embedding)
        except Exception as _save_exc:
            LOG.debug("Vector memory save skipped: %s", _save_exc)

        result["session_id"] = session_id
        return result

    except ValueError as exc:
        msg = str(exc)
        # "No AI agent could complete the question" means API keys or quota issue — surface cleanly
        if "No AI agent could complete" in msg:
            raise HTTPException(status_code=503, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
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
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    uploads_dir = run_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    try:
        saved_paths, reference_uploads = await _persist_uploaded_files(uploads_dir, files)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"TriConvey import failed while resolving cached files: {exc}",
        ) from exc

    saved_files: list[str] = []
    for destination in saved_paths:
        saved_files.append(destination.name)
        try:
            document = load_pdf_document(destination)
            prime_cached_pdf_analysis(destination, document)
        except Exception:
            pass

    if not saved_files:
        if reference_uploads:
            raise HTTPException(
                status_code=400,
                detail=(
                    "TriConvey drop detected, but no accessible PDFs were found from the provided Smokeball paths. "
                    "Open each document in TriConvey first so Smokeball caches it locally, then drag the files again."
                ),
            )
        raise HTTPException(status_code=400, detail="No valid PDF files were uploaded.")

    for path in (
        run_dir / "document_corpus_manifest.json",
        run_dir / "document_memory.json",
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    # For new chat documents, use the incremental processor (single-doc, not full re-run).
    # Each file gets its own processing job. Results are returned to the UI as pending.
    saved_settings = load_local_settings(user_id=str(ctx.user.id))
    corpus_model = saved_settings.get("defaultModelName") or "gpt-4.1-mini"
    matter_id = str(run.matter_id) if run.matter_id else run_id

    pending_doc_ids = []
    for dest in saved_paths:
        doc_id = str(uuid4())
        pending_doc_ids.append({"document_id": doc_id, "filename": dest.name})
        extract_corpus_entry_async(
            run_dir=run_dir,
            document_path=dest,
            document_id=doc_id,
            matter_id=matter_id,
            run_id=run_id,
            model=corpus_model,
        )

    warm_brain_f_assets_async(run_dir)

    return {
        "uploaded": saved_files,
        "message": f"Uploaded {len(saved_files)} file(s). Processing — confirm new information in the chat.",
        "corpus_extraction_started": len(saved_files) > 0,
        "pending_documents": pending_doc_ids,
    }


@app.post("/api/runs/{run_id}/reprocess")
async def reprocess_run(
    run_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Re-run the full extraction pipeline on an existing run (original + chat-uploaded docs).

    Called automatically after chat file uploads so new documents are fully
    extracted and their facts merged into the answers.json for the run.
    """
    import asyncio
    from functools import partial

    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    uploads_dir = run_dir / "uploads"

    # Collect all documents (PDF + Word) from both the run root and uploads subdir
    doc_paths: list[Path] = []
    for search_dir in (uploads_dir, run_dir):
        if search_dir.exists():
            doc_paths.extend(
                p for p in search_dir.iterdir()
                if p.is_file() and p.suffix.lower() in (".pdf", ".docx", ".doc")
            )
    # Deduplicate by name (uploads/ takes priority)
    seen: set[str] = set()
    unique_paths: list[Path] = []
    for p in doc_paths:
        if p.name not in seen:
            seen.add(p.name)
            unique_paths.append(p)

    if not unique_paths:
        raise HTTPException(status_code=400, detail="No documents found to reprocess.")

    saved_settings = load_local_settings(user_id=str(ctx.user.id))
    model = run.model or saved_settings.get("defaultModelName", "gpt-4.1-mini")
    use_ai_review = bool(run.use_ai_review)
    water_copy_rules = await _fetch_water_copy_rules(session, client_id=ctx.client.id)

    try:
        loop = asyncio.get_event_loop()
        pipeline_fn = partial(
            build_review_run,
            unique_paths,
            run_dir=run_dir,
            use_ai_review=use_ai_review,
            model=model,
            copy_rules=water_copy_rules,
        )
        payload: dict[str, Any] = await loop.run_in_executor(None, pipeline_fn)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Reprocess failed: {exc}") from exc

    await _apply_copy_rule_fallbacks_to_run(session, client_id=ctx.client.id, run_dir=run_dir)
    payload = load_run_payload(run_dir)
    await _persist_run_to_db(session, payload, client_id=ctx.client.id, run_uuid=run_uuid)
    # Bust Brain F caches so the next chat uses fresh facts
    for cache_file in (
        run_dir / "document_corpus_manifest.json",
        run_dir / "document_memory.json",
    ):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
    warm_brain_f_assets_async(run_dir)
    return payload


# ---------------------------------------------------------------------------
# Incremental single-document processing endpoints
# ---------------------------------------------------------------------------


class ProcessDocumentRequest(BaseModel):
    filename: str
    model: str | None = None


class ApplyDocumentChangesRequest(BaseModel):
    document_id: str
    doc_path: str                               # path on disk (returned by upload)
    corpus_entry: dict[str, Any] | None = None
    approved_change_ids: list[str] = []
    all_changes: list[dict[str, Any]] = []


@app.post("/api/runs/{run_id}/chat-documents/process")
async def process_chat_document_endpoint(
    run_id: str,
    filename: str = Form(...),
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Upload and incrementally process a single new document.

    1. Saves the file
    2. Validates relevance (warns if suspicious)
    3. Extracts facts + corpus entry from this doc only
    4. Returns proposed answer changes for user approval

    The caller (Chatbot UI) shows the validation result and proposed changes
    before the user approves or rejects.
    """
    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    uploads_dir = run_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Save the uploaded file
    safe_name = Path(file.filename or filename).name
    dest_path = uploads_dir / safe_name
    try:
        content = await file.read()
        dest_path.write_bytes(content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not save file: {exc}")

    saved_settings = load_local_settings(user_id=str(ctx.user.id))
    model = saved_settings.get("defaultModelName") or "gpt-4.1-mini"
    matter_id = str(run.matter_id) if run.matter_id else run_id
    copy_rules = await CopyRuleRepo.list_for_client(
        session,
        client_id=ctx.client.id,
        rule_type="water_authority",
        include_inactive=False,
    )

    # If this is a TriConvey reference file, resolve it to the actual PDF(s)
    if _looks_like_triconvey_reference(dest_path):
        resolved_pdfs = _resolve_triconvey_reference_upload(dest_path)
        # Copy resolved PDFs into uploads and clean up the reference file
        dest_path.unlink(missing_ok=True)
        if not resolved_pdfs:
            return {
                "error": "TriConvey reference resolved to no accessible documents. Open the document in TriConvey first so it is cached locally.",
                "doc_path": None,
                "proposed_changes": [],
                "corpus_entry": None,
            }
        dest_path = _copy_into_uploads(resolved_pdfs[0], uploads_dir)

    # Check for duplicate: if this file is already in the uploads, warn the user
    _doc_suffixes = (".pdf", ".docx", ".doc")
    existing_uploads = {p.name for p in uploads_dir.iterdir() if p.is_file() and p != dest_path and p.suffix.lower() in _doc_suffixes}
    if dest_path.name in existing_uploads or any(
        p.stem == dest_path.stem for p in uploads_dir.iterdir()
        if p.is_file() and p != dest_path and p.suffix.lower() in _doc_suffixes
    ):
        return {
            "already_exists": True,
            "filename": dest_path.name,
            "doc_path": str(dest_path),
            "proposed_changes": [],
            "corpus_entry": None,
            "message": f"'{dest_path.name}' is already part of this matter. Do you want to re-process it with the latest version?",
        }

    # Run incremental processing (synchronous — returns in ~10-20 seconds)
    loop = asyncio.get_event_loop()
    from functools import partial
    try:
        result = await loop.run_in_executor(
            None,
            partial(
                process_chat_document,
                run_dir,
                dest_path,
                model,
                matter_id,
                run_id,
                [(row.authority_name, float(row.annual_amount)) for row in copy_rules],
            ),
        )
    except Exception as exc:
        LOG.exception("process_chat_document failed for %s", dest_path.name)
        return {
            "error": str(exc),
            "doc_path": str(dest_path),
            "proposed_changes": [],
            "corpus_entry": None,
        }
    result["doc_path"] = str(dest_path)
    return result


@app.post("/api/runs/{run_id}/chat-documents/apply")
async def apply_chat_document_changes_endpoint(
    run_id: str,
    body: ApplyDocumentChangesRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Apply user-approved answer changes from an incremental document processing result."""
    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    matter_id = str(run.matter_id) if run.matter_id else run_id

    result = apply_chat_document_changes(
        run_dir=run_dir,
        document_id=body.document_id,
        doc_path=body.doc_path,
        corpus_entry_dict=body.corpus_entry,
        approved_change_ids=body.approved_change_ids,
        all_changes=body.all_changes,
        matter_id=matter_id,
        run_id=run_id,
    )

    # Bust Brain F caches so the next chat uses the updated corpus
    for cache_file in (run_dir / "document_corpus_manifest.json", run_dir / "document_memory.json"):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
    warm_brain_f_assets_async(run_dir)
    await _apply_copy_rule_fallbacks_to_run(session, client_id=ctx.client.id, run_dir=run_dir)
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Corpus endpoints
# ---------------------------------------------------------------------------


class CorpusConfirmRequest(BaseModel):
    document_id: str


@app.get("/api/runs/{run_id}/corpus")
async def get_run_corpus(
    run_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the structured document corpus for a run (confirmed + pending)."""
    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    matter_id = str(run.matter_id) if run.matter_id else run_id
    return get_corpus_state(run_dir, matter_id=matter_id, run_id=run_id)


@app.post("/api/runs/{run_id}/corpus/confirm")
async def confirm_corpus_entry(
    run_id: str,
    body: CorpusConfirmRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Confirm a pending corpus entry — user has reviewed and approved the extracted information."""
    try:
        run_uuid = _uuid.UUID(run_id)
        run = await RunRepo.get(session, client_id=ctx.client.id, run_id=run_uuid, user_id=ctx.user.id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    run_dir = _resolve_run_dir(run_id)
    matter_id = str(run.matter_id) if run.matter_id else run_id
    confirmed = confirm_corpus_document(run_dir, body.document_id, matter_id=matter_id, run_id=run_id)
    if confirmed is None:
        raise HTTPException(status_code=404, detail=f"No pending corpus entry for document '{body.document_id}'.")

    return {
        "confirmed": True,
        "document_id": body.document_id,
        "filename": confirmed.get("filename"),
        "message": f"Document corpus updated with information from {confirmed.get('filename', 'document')}.",
    }


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


@app.post("/api/triconvey/resolve-reference")
async def resolve_triconvey_reference(
    body: ResolveTriconveyReferenceRequest,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Resolve TriConvey/Smokeball drag payload to concrete local PDFs.

    Used by the UI to display real PDF filenames for drag-and-drop references
    before the user starts a full extraction run.
    """
    del ctx  # auth gate only
    raw = (body.payload_text or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="payload_text is required.")
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object.")

    _triconvey_import_debug(
        "ui_resolve_reference_start",
        keys=list(payload.keys()),
        matter_id=str(payload.get("MatterId") or ""),
        file_ids=payload.get("Files") if isinstance(payload.get("Files"), list) else [],
    )

    try:
        resolved_paths = _resolve_triconvey_reference_payload(payload)
    except Exception as exc:
        _triconvey_import_debug("ui_resolve_reference_error", error=exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved = [{"name": path.name, "path": str(path)} for path in resolved_paths]
    display_name = resolved[0]["name"] if resolved else ""
    subtitle = f"Resolved {len(resolved)} document(s)" if resolved else "No local documents resolved yet"

    _triconvey_import_debug(
        "ui_resolve_reference_done",
        resolved_count=len(resolved),
        resolved_names=[item["name"] for item in resolved],
    )

    return {
        "resolved": resolved,
        "display_name": display_name,
        "subtitle": subtitle,
    }


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


@app.get("/api/autofill-jobs/{job_id}/activity")
async def get_autofill_job_activity(
    job_id: str,
    cursor: int = 0,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    with _autofill_lock:
        record = _autofill_jobs.get(job_id)
        context = _autofill_job_context.get(job_id)
    if record is None or context is None:
        raise HTTPException(status_code=404, detail=f"Autofill job '{job_id}' was not found.")

    run_dir = Path(context["run_dir"])
    live = _load_live_execution_session(run_dir)
    event_log_path = Path(live["event_log_path"]) if live.get("event_log_path") else None
    screenshots_dir = Path(live["screenshots_dir"]) if live.get("screenshots_dir") else None

    events: list[dict[str, Any]] = []
    next_cursor = cursor
    if event_log_path is not None:
        events, next_cursor = _read_autofill_events(event_log_path, max(cursor, 0))

    latest_screenshot_name = None
    latest_screenshot_url = None
    if screenshots_dir is not None and screenshots_dir.exists():
        latest = max(
            (path for path in screenshots_dir.glob("*.png") if path.is_file()),
            default=None,
            key=lambda item: item.stat().st_mtime,
        )
        if latest is not None:
            latest_screenshot_name = latest.name
            latest_screenshot_url = f"/api/autofill-jobs/{job_id}/latest-screenshot"

    payload = AutofillActivityPayload(
        job_id=job_id,
        status=record.status,
        cursor=next_cursor,
        events=[AutofillActivityEvent.model_validate(item) for item in events],
        latest_screenshot_url=latest_screenshot_url,
        latest_screenshot_name=latest_screenshot_name,
    )
    return payload.model_dump(mode="json")


@app.get("/api/autofill-jobs/{job_id}/latest-screenshot")
async def get_autofill_job_latest_screenshot(
    job_id: str,
    ctx: AuthContext = Depends(require_auth),
):
    with _autofill_lock:
        context = _autofill_job_context.get(job_id)
    if context is None:
        raise HTTPException(status_code=404, detail=f"Autofill job '{job_id}' was not found.")

    run_dir = Path(context["run_dir"])
    live = _load_live_execution_session(run_dir)
    screenshots_dir = Path(live["screenshots_dir"]) if live.get("screenshots_dir") else None
    if screenshots_dir is None or not screenshots_dir.exists():
        raise HTTPException(status_code=404, detail="No screenshots are available yet.")

    latest = max(
        (path for path in screenshots_dir.glob("*.png") if path.is_file()),
        default=None,
        key=lambda item: item.stat().st_mtime,
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="No screenshots are available yet.")
    return FileResponse(latest, media_type="image/png", filename=latest.name)


@app.get("/api/runs/{run_id}/brain-e-logs")
async def export_brain_e_logs(
    run_id: str,
    ctx: AuthContext = Depends(require_auth),
):
    run_dir = _resolve_run_dir(run_id)
    members = _brain_e_export_members(run_dir)
    if not members:
        raise HTTPException(status_code=404, detail="No Brain E logs are available for this run yet.")

    runtime = ensure_runtime_dirs()
    export_dir = runtime.temp_dir / "brain_e_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / f"brain_e_logs_{run_id}.zip"
    if export_path.exists():
        try:
            export_path.unlink()
        except Exception:
            pass

    with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, arcname in members:
            try:
                archive.write(source, arcname)
            except Exception:
                continue

    return FileResponse(
        export_path,
        media_type="application/zip",
        filename=export_path.name,
        background=BackgroundTask(lambda path=export_path: path.unlink(missing_ok=True)),
    )


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
    # Do NOT force-stop TriConvey — the user may have that matter open and
    # wants to continue working in it. The cancel_event signals the background
    # thread to abort its next interruptible_sleep/action loop gracefully.
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


# ── Smokeball direct-push endpoint ───────────────────────────────────────────

def _smokeball_debug(event: str, **fields: Any) -> None:
    """Append a structured line to smokeball_push_debug.log."""
    try:
        runtime = ensure_runtime_dirs()
        log_file = runtime.local_app_dir / "smokeball_push_debug.log"
        timestamp = datetime.now(UTC).isoformat()
        parts = [f"ts={timestamp}", f"event={event}"]
        for key, value in fields.items():
            text = str(value).replace("\n", " ").replace("\r", " ")[:500]
            parts.append(f"{key}={text}")
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(" | ".join(parts) + "\n")
    except Exception:
        pass


class SmokeballPushRequest(BaseModel):
    matter_number: str
    answers: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/smokeball/push-s32")
async def push_s32_to_smokeball(
    body: SmokeballPushRequest,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Push Section 32 conveyancing fields directly into Smokeball/TriConvey."""
    # Apply client policy filter — only push fields that are starred in custom policy.
    _settings = load_local_settings(user_id=str(ctx.user.id))
    _preferred = {str(f).strip() for f in (_settings.get("preferredAutofillFields") or []) if str(f).strip()}
    if _preferred:
        body.answers = {k: v for k, v in body.answers.items() if k in _preferred}

    _smokeball_debug("push_s32_start", matter_number=body.matter_number, answer_keys=list(body.answers.keys()))
    try:
        from triconvey_agent.smokeball.client import push_s32_to_matter
    except ImportError as exc:
        _smokeball_debug("push_s32_import_error", error=exc)
        raise HTTPException(status_code=500, detail=f"Smokeball client unavailable: {exc}")

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: push_s32_to_matter(body.matter_number, body.answers),
        )
        _smokeball_debug("push_s32_result",
                         success=result.get("success"),
                         method=result.get("method"),
                         fields_pushed=result.get("fields_pushed"),
                         error=result.get("error"),
                         warning=result.get("warning"))
    except RuntimeError as exc:
        _smokeball_debug("push_s32_runtime_error", error=exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        LOG.exception("push_s32_to_smokeball failed")
        _smokeball_debug("push_s32_exception", error=repr(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error", "Push failed"))

    return result


@app.get("/api/smokeball/matters")
async def list_smokeball_matters(
    ctx: AuthContext = Depends(require_auth),
) -> list[dict[str, Any]]:
    """Return all matters from the Smokeball browse-graphql endpoint.

    triConvey.exe MUST be running and logged in.
    """
    try:
        from triconvey_agent.smokeball.client import SmokeballClient
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Smokeball client unavailable: {exc}")

    try:
        def _fetch() -> list[dict]:
            client = SmokeballClient()
            return client.list_matters(limit=200)

        matters = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        LOG.exception("list_smokeball_matters failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return matters


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


def _load_live_execution_session(run_dir: Path) -> dict[str, Any]:
    live_path = run_dir / "execution_artifacts" / "live_session.json"
    if live_path.exists():
        try:
            return json.loads(live_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    report_path = run_dir / "execution_report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {
            "diagnostics_dir": report.get("diagnostics_dir"),
            "event_log_path": report.get("event_log_path"),
            "debug_log_path": report.get("debug_log_path"),
            "screenshots_dir": str(Path(report["diagnostics_dir"]) / "screenshots")
            if report.get("diagnostics_dir")
            else None,
        }
    return {}


def _brain_e_export_members(run_dir: Path) -> list[tuple[Path, str]]:
    members: list[tuple[Path, str]] = []
    report_path = run_dir / "execution_report.json"
    if report_path.exists():
        members.append((report_path, "execution_report.json"))

    live = _load_live_execution_session(run_dir)
    diagnostics_dir = Path(live["diagnostics_dir"]) if live.get("diagnostics_dir") else None
    if diagnostics_dir is not None and diagnostics_dir.exists():
        for candidate_name in ("events.jsonl", "autofill_debug.log", "live_session.json"):
            candidate = diagnostics_dir / candidate_name
            if candidate.exists():
                members.append((candidate, f"execution_artifacts/{candidate_name}"))

    runtime = ensure_runtime_dirs()
    brain_e_app_dir = runtime.local_app_dir / "brain_e"
    brain_e_cache_dir = runtime.cache_dir / "brain_e"
    for candidate, target_name in (
        (brain_e_cache_dir / "brain_e_debug.jsonl", "brain_e_debug.jsonl"),
        (brain_e_app_dir / "brain_e_learning.jsonl", "brain_e_learning.jsonl"),
        (brain_e_app_dir / "brain_e_debug_summary.json", "brain_e_debug_summary.json"),
        (brain_e_app_dir / "brain_e_debug_summary.txt", "brain_e_debug_summary.txt"),
        (brain_e_app_dir / "learning_profile.json", "brain_e_learning_profile.json"),
    ):
        if candidate.exists():
            members.append((candidate, target_name))
    return members


def _summarize_autofill_event(kind: str, payload: dict[str, Any]) -> str:
    if kind == "execution_start":
        return f"Autofill started for {payload.get('client_name') or 'the current matter'}."
    if kind == "execution_complete":
        return "Autofill finished running the action plan."
    if kind == "execution_cancelled":
        return f"Autofill was cancelled at {payload.get('stage') or 'an unknown stage'}."
    if kind == "property_details_already_open_skipping_matter_search":
        return "Property Details was already open, so matter search was skipped."
    if kind == "matter_window_already_open_skipping_search":
        return "The matter window was already open, so search was skipped."
    if kind == "property_details_visible_after_matter_open":
        return f"Property Details became visible after opening the matter (attempt {payload.get('attempt')})."
    if kind == "property_details_not_visible_after_matter_open":
        return f"Waiting for Property Details to appear (attempt {payload.get('attempt')})."
    if kind == "action_start":
        tab = payload.get("tab") or "Unknown tab"
        action = payload.get("action") or "act"
        qid = payload.get("question_id") or "field"
        value = payload.get("payload_preview")
        suffix = f" -> {value}" if value not in (None, "") else ""
        return f"{tab}: {action} on {qid}{suffix}"
    if kind == "action_result":
        qid = payload.get("question_id") or "field"
        status = payload.get("status") or "unknown"
        error = payload.get("error")
        return f"{qid}: {status}" + (f" ({error})" if error else "")
    if kind == "locator_resolved":
        return f"{payload.get('question_id') or 'field'} located via {payload.get('locator_strategy') or 'unknown strategy'}."
    if kind == "preflight_check":
        return f"Preflight {payload.get('name')}: {payload.get('status')} ({payload.get('detail')})"
    if kind == "log":
        return str(payload.get("message") or "").strip() or "Autofill log updated."
    if kind == "screenshot_error":
        return f"Screenshot capture failed for {payload.get('name') or 'step'}."
    return kind.replace("_", " ")


def _read_autofill_events(event_log_path: Path, cursor: int) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    next_cursor = cursor
    try:
        with event_log_path.open("r", encoding="utf-8") as fh:
            for index, line in enumerate(fh):
                if index < cursor:
                    continue
                next_cursor = index + 1
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                kind = str(payload.get("kind") or "log")
                summary = _summarize_autofill_event(kind, payload)
                events.append(
                    {
                        "index": index,
                        "ts": payload.get("ts"),
                        "kind": kind,
                        "summary": summary,
                        "payload": payload,
                    }
                )
    except FileNotFoundError:
        return [], cursor
    return events, next_cursor


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
    explicit_path_values = _extract_triconvey_explicit_path_values(payload)
    if explicit_path_values:
        _triconvey_import_debug("reference_payload_explicit_paths", count=len(explicit_path_values))
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

    file_ids = [str(value or "").strip() for value in payload.get("Files", []) if str(value or "").strip()]
    if file_ids:
        _triconvey_import_debug("resolve_payload_try_files2", matter_id=matter_id, file_ids=file_ids)
        files2_pdfs = _resolve_smokeball_files2_paths(file_ids)
        if files2_pdfs:
            result = _limit_candidates(files2_pdfs)
            _triconvey_import_debug("resolve_payload_files2_hits", matter_id=matter_id, count=len(result), names=[p.name for p in result])
            LOG.info(
                "Resolved %d PDF(s) from Smokeball files2 for matter %s: %s",
                len(result), matter_id, [p.name for p in result],
            )
            return result

    # --- Primary: %TEMP%\{hex}\{hex}\{hex}\*.pdf (WebView2 download cache) ----
    _hex_dir = _re.compile(r"^[0-9a-f]{5,9}$", _re.I)

    def _collect_hex3_docs(root: Path) -> list[Path]:
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
                        docs = [
                            p for p in l3.iterdir()
                            if p.is_file() and p.suffix.lower() in (".pdf", ".docx", ".doc")
                        ]
                    except OSError:
                        continue
                    for doc in docs:
                        if doc in seen:
                            continue
                        try:
                            mtime = datetime.fromtimestamp(doc.stat().st_mtime, tz=UTC)
                        except OSError:
                            continue
                        if mtime >= cutoff:
                            seen.add(doc)
                            found.append(doc)
        return found

    webview2_pdfs = _collect_hex3_docs(temp_root)
    if webview2_pdfs:
        result = _limit_candidates(webview2_pdfs)
        _triconvey_import_debug("resolve_payload_webview2_hits", matter_id=matter_id, count=len(result), names=[p.name for p in result])
        LOG.info(
            "Resolved %d document(s) from WebView2 temp cache for matter %s: %s",
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
                docs = [
                    p for p in sub_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in (".pdf", ".docx", ".doc")
                ]
            except OSError:
                continue
            for doc in docs:
                try:
                    mtime = datetime.fromtimestamp(doc.stat().st_mtime, tz=UTC)
                except OSError:
                    continue
                if mtime >= cutoff:
                    fallback.append(doc)

    if fallback:
        result = _limit_candidates(fallback)
        _triconvey_import_debug("resolve_payload_smokeball_hits", matter_id=matter_id, count=len(result), names=[p.name for p in result])
        LOG.info(
            "Resolved %d document(s) from Smokeball app cache for matter %s: %s",
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
    explicit_paths = _extract_triconvey_explicit_path_values(payload)
    wait_for_triconvey_paths(explicit_paths, sleeper=time.sleep, delay_seconds=2.0)
    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw_value in explicit_paths:
        path = _normalize_triconvey_local_path(str(raw_value or ""))
        _triconvey_import_debug("explicit_path_candidate", raw=raw_value, normalized=path or "<none>")
        if path is None or path in seen or not path.exists():
            continue
        seen.add(path)
        if path.suffix.lower() in (".pdf", ".docx", ".doc"):
            resolved.append(path)
            continue
        if _looks_like_triconvey_reference(path):
            resolved.extend(_resolve_triconvey_reference_upload(path))
    if resolved:
        _triconvey_import_debug("explicit_path_hits", count=len(resolved), names=[p.name for p in resolved])
        LOG.info("Resolved %d document(s) from explicit TriConvey local paths: %s", len(resolved), [p.name for p in resolved])
    return _dedupe_paths(resolved)


def _extract_triconvey_explicit_path_values(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()

    def _push(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        text = candidate.strip()
        if not text or text in seen:
            return
        seen.add(text)
        values.append(text)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                normalized_key = re.sub(r"[^a-z0-9]+", "", str(key).lower())
                if normalized_key in {"localpath", "localpaths", "filepath", "filepaths", "pdfpath", "pdfpaths"}:
                    if isinstance(value, list):
                        for item in value:
                            _push(item)
                    else:
                        _push(value)
                else:
                    _walk(value)
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return values


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


def _try_decode_smokeball_folder_name(value: str) -> str | None:
    try:
        decoded = base64.b64decode(value)
        return decoded.decode("utf-8", errors="strict")
    except Exception:
        return None


def _resolve_smokeball_files2_paths(file_ids: list[str]) -> list[Path]:
    base_dir = Path(os.getenv("CONVEY_SMOKEBALL_FILES2_DIR", r"C:\Program Files\Smokeball\dataAu\mattermanagement\files2"))
    if not base_dir.exists() or not base_dir.is_dir():
        _triconvey_import_debug("smokeball_files2_base_missing", base_dir=base_dir)
        return []

    _triconvey_import_debug("smokeball_files2_scan_start", base_dir=base_dir, file_ids=file_ids)

    matched_pdfs: list[Path] = []
    seen_paths: set[str] = set()
    try:
        folders = list(base_dir.iterdir())
    except OSError as exc:
        _triconvey_import_debug("smokeball_files2_scan_error", base_dir=base_dir, error=exc)
        return []

    for raw_file_id in file_ids:
        file_id = str(raw_file_id or "").strip()
        if not file_id:
            continue
        matched_folder: Path | None = None
        decoded_name: str | None = None

        for folder in folders:
            if not folder.is_dir():
                continue
            decoded = _try_decode_smokeball_folder_name(folder.name)
            if not decoded:
                continue
            if decoded.lower().startswith(file_id.lower()):
                matched_folder = folder
                decoded_name = decoded
                break

        if matched_folder is None:
            _triconvey_import_debug("smokeball_files2_fileid_miss", file_id=file_id, scanned_folder_count=len(folders))
            continue

        try:
            pdfs = sorted(
                [path for path in matched_folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError as exc:
            _triconvey_import_debug("smokeball_files2_folder_read_error", file_id=file_id, folder=matched_folder, error=exc)
            continue

        _triconvey_import_debug(
            "smokeball_files2_fileid_hit",
            file_id=file_id,
            folder=matched_folder,
            decoded_name=decoded_name or "",
            pdf_count=len(pdfs),
            pdf_names=[path.name for path in pdfs],
        )

        for pdf in pdfs:
            key = str(pdf.resolve()) if pdf.exists() else str(pdf)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            matched_pdfs.append(pdf)

    _triconvey_import_debug("smokeball_files2_scan_done", matched_pdf_count=len(matched_pdfs), pdf_names=[path.name for path in matched_pdfs])
    return matched_pdfs


async def _persist_uploaded_files(
    uploads_dir: Path,
    files: list[UploadFile],
) -> tuple[list[Path], list[Path]]:
    saved_paths: list[Path] = []
    reference_uploads: list[Path] = []
    _triconvey_import_debug(
        "persist_uploads_start",
        uploads_dir=uploads_dir,
        file_count=len(files),
        filenames=[getattr(upload, "filename", "") or "<unnamed>" for upload in files],
    )

    for upload in files:
        if not upload.filename:
            _triconvey_import_debug("persist_upload_skipped_empty_name")
            continue
        destination = uploads_dir / _safe_filename(upload.filename)
        with destination.open("wb") as output_stream:
            shutil.copyfileobj(upload.file, output_stream)
        try:
            size = destination.stat().st_size
        except OSError:
            size = -1
        if destination.suffix.lower() in (".pdf", ".docx", ".doc"):
            saved_paths.append(destination)
            _triconvey_import_debug("persist_upload_saved_pdf", source_name=upload.filename, destination=destination, size=size)
        elif _looks_like_triconvey_reference(destination):
            reference_uploads.append(destination)
            _triconvey_import_debug("persist_upload_saved_reference", source_name=upload.filename, destination=destination, size=size)
        else:
            _triconvey_import_debug("persist_upload_saved_other", source_name=upload.filename, destination=destination, size=size)
        await upload.close()

    resolved_reference_pdfs: list[Path] = []
    for reference_path in _dedupe_paths(reference_uploads):
        _triconvey_import_debug("persist_upload_resolving_reference", path=reference_path)
        resolved_reference_pdfs.extend(_resolve_triconvey_reference_upload(reference_path))

    _triconvey_import_debug(
        "persist_upload_resolved_reference_results",
        resolved_count=len(resolved_reference_pdfs),
        resolved_names=[path.name for path in resolved_reference_pdfs],
    )

    for resolved_pdf in _dedupe_paths(resolved_reference_pdfs):
        copied = _copy_into_uploads(resolved_pdf, uploads_dir)
        if copied not in saved_paths:
            saved_paths.append(copied)
        _triconvey_import_debug("persist_upload_copied_resolved_pdf", source=resolved_pdf, copied=copied)

    _triconvey_import_debug(
        "persist_uploads_done",
        saved_pdf_count=len(saved_paths),
        reference_count=len(reference_uploads),
        saved_names=[path.name for path in saved_paths],
        reference_names=[path.name for path in reference_uploads],
    )

    return _dedupe_paths(saved_paths), _dedupe_paths(reference_uploads)


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
            await ensure_runtime_schema()
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
        MatterRepo,
        RunRepo,
    )

    manifest = payload.get("manifest", {})
    metrics = payload.get("metrics", {})
    doc_count: int = manifest.get("document_count", 0)
    total_facts: int = manifest.get("total_facts", 0)
    matter_payload = payload.get("matter", {}) if isinstance(payload.get("matter"), dict) else {}

    matter_ref = str(matter_payload.get("matter_ref") or "").strip() or None
    volume_folio = str(matter_payload.get("volume_folio") or "").strip() or None
    property_address = str(matter_payload.get("property_address") or "").strip() or None

    if matter_ref or volume_folio or property_address:
        matter = await MatterRepo.upsert(
            session,
            client_id=client_id,
            matter_ref=matter_ref,
            property_address=property_address,
            volume_folio=volume_folio,
            status="active",
        )
        matter.last_run_id = run_uuid
        matter.last_run_at = datetime.now(UTC)
        await RunRepo.attach_matter(
            session,
            client_id=client_id,
            run_id=run_uuid,
            matter_id=matter.id,
        )

    # 1. Update run row with final status + metrics.
    await RunRepo.update_status(
        session,
        client_id=client_id,
        run_id=run_uuid,
        status="completed",
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
