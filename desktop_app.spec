# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

project_root = Path.cwd()
icon_path = os.environ.get("TRICONVEY_APP_ICON") or None
version_file = os.environ.get("TRICONVEY_VERSION_FILE") or None

datas = [
    (str(project_root / "ui" / "dist"), "ui/dist"),
    (str(project_root / "triconvey-mapping"), "triconvey-mapping"),
    (str(project_root / "src" / "triconvey_agent" / "canonical" / "questions" / "registry.yaml"),
     "triconvey_agent/canonical/questions"),
    (str(project_root / "src" / "triconvey_agent" / "db" / "schema.sql"),
     "triconvey_agent/db"),
]

hiddenimports = [
    # --- Backend ---
    "triconvey_agent.backend.api",
    "triconvey_agent.backend.service",
    "triconvey_agent.backend.runtime",
    "triconvey_agent.backend.settings",
    # --- Auth ---
    "triconvey_agent.auth.security",
    "triconvey_agent.auth.oauth",
    "triconvey_agent.auth.deps",
    # --- Database ---
    "triconvey_agent.db.session",
    "triconvey_agent.db.models",
    "triconvey_agent.db.repositories",
    "triconvey_agent.db.bootstrap",
    # --- Config ---
    "triconvey_agent.config",
    # --- AI / OpenAI ---
    "triconvey_agent.ai.openai_client",
    "triconvey_agent.ai.client",
    # --- Canonical pipeline ---
    "triconvey_agent.canonical.schemas",
    "triconvey_agent.canonical.brain_d",
    "triconvey_agent.canonical.brain_d.mapper",
    "triconvey_agent.canonical.brain_d.field_map",
    "triconvey_agent.canonical.brain_e",
    "triconvey_agent.canonical.brain_e.executor",
    "triconvey_agent.canonical.extractors",
    "triconvey_agent.canonical.extractors.building_approval",
    "triconvey_agent.canonical.extractors.generic_doc_meta",
    "triconvey_agent.canonical.extractors.land_tax_certificate",
    "triconvey_agent.canonical.extractors.owners_corporation",
    "triconvey_agent.canonical.extractors.paths",
    "triconvey_agent.canonical.extractors.planning_certificate",
    "triconvey_agent.canonical.extractors.vendor_form",
    "triconvey_agent.canonical.extractors.vic_title",
    "triconvey_agent.canonical.extractors.water_authority_certificate",
    "triconvey_agent.canonical.facts",
    "triconvey_agent.canonical.facts.authority",
    "triconvey_agent.canonical.facts.store",
    "triconvey_agent.canonical.policy",
    "triconvey_agent.canonical.policy.attachments",
    "triconvey_agent.canonical.policy.computed",
    "triconvey_agent.canonical.policy.defaults",
    "triconvey_agent.canonical.questions",
    "triconvey_agent.canonical.questions.loader",
    "triconvey_agent.canonical.router",
    "triconvey_agent.canonical.router.router",
    "triconvey_agent.canonical.runner",
    "triconvey_agent.canonical.runner.ai_review",
    "triconvey_agent.canonical.runner.fact_extraction",
    "triconvey_agent.canonical.runner.summary_writer",
    # --- Brain F (AI Agent) ---
    "triconvey_agent.brain_f.agent",
    "triconvey_agent.brain_f.tools",
    "triconvey_agent.brain_f.prompts",
    "triconvey_agent.brain_f.cache",
    "triconvey_agent.brain_f.memory",
    # --- Sync ---
    "triconvey_agent.sync.worker",
    # --- Ingest ---
    "triconvey_agent.ingest",
    "triconvey_agent.ingest.pdf_loader",
    "triconvey_agent.ingest.normalizer",
    "triconvey_agent.ingest.yaml_loader",
    # --- Schemas ---
    "triconvey_agent.schemas",
    "triconvey_agent.schemas.documents",
    # --- Third-party (conditional / lazy imports) ---
    "pywinauto",
    "pytesseract",
    "PIL",
    "PIL.ImageGrab",
    "PIL.ImageFilter",
    "PIL.ImageOps",
    "openai",
    "dotenv",
    "yaml",
    "multipart",
    # --- Database drivers ---
    "aiosqlite",
    "asyncpg",
    # --- uvicorn internals ---
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

a = Analysis(
    ["desktop_app.py"],
    pathex=[str(project_root), str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TriConveyAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_file,
    icon=icon_path,
)
