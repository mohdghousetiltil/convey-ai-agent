from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import uvicorn
import webview


# ---------------------------------------------------------------------------
# Debug file logging
# ---------------------------------------------------------------------------

class _TeeStream:
    """Write to both the original stream and a log file simultaneously."""

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data: str) -> int:
        try:
            self._original.write(data)
            self._original.flush()
        except Exception:
            pass
        try:
            self._log_file.write(data)
            self._log_file.flush()
        except Exception:
            pass
        return len(data)

    def flush(self) -> None:
        try:
            self._original.flush()
        except Exception:
            pass
        try:
            self._log_file.flush()
        except Exception:
            pass

    def fileno(self):
        return self._original.fileno()

    def isatty(self) -> bool:
        return False


def _setup_debug_logging() -> Path | None:
    """Set up file-based debug logging to %LOCALAPPDATA%/TriConveyAgent/temp/ConveyAgent-debug-*.log.

    Captures ALL Python logging output (DEBUG+), stdout, and stderr so the
    full picture of what the app is doing is in one readable file.
    Returns the log file path, or None if it could not be created.
    """
    try:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            log_dir = Path(local_app_data) / "TriConveyAgent" / "temp"
        else:
            # Fallback for non-Windows or unusual setups
            import tempfile
            log_dir = Path(tempfile.gettempdir()) / "TriConveyAgent"

        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_path = log_dir / f"ConveyAgent-debug-{timestamp}.log"
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)  # line-buffered

        # Header
        log_file.write(f"=== Convey Agent Debug Log ===\n")
        log_file.write(f"Started : {datetime.now().isoformat()}\n")
        log_file.write(f"Python  : {sys.version}\n")
        log_file.write(f"Frozen  : {getattr(sys, 'frozen', False)}\n")
        log_file.write(f"Exe     : {sys.executable}\n")
        log_file.write("=" * 60 + "\n\n")

        # Python logging — capture everything at DEBUG level
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8", delay=False)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root_logger.addHandler(file_handler)

        # Silence extremely verbose internal loggers that produce thousands of
        # lines per request and obscure the app-level logs we actually care about.
        for _noisy in (
            "aiosqlite",
            "sqlalchemy.engine",
            "sqlalchemy.pool",
            "sqlalchemy.dialects",
            "sqlalchemy.orm",
            "asyncio",
            "urllib3",
            "httpcore",
            "httpx",
            "multiprocessing",
            "uvicorn.access",           # HTTP access log kept at INFO via uvicorn itself
            "python_multipart",         # multipart file-upload byte-level noise
            "python_multipart.multipart",
        ):
            logging.getLogger(_noisy).setLevel(logging.WARNING)

        # Tee stdout and stderr so print() and traceback output also land in the file
        sys.stdout = _TeeStream(sys.__stdout__, log_file)
        sys.stderr = _TeeStream(sys.__stderr__, log_file)

        return log_path
    except Exception as exc:
        try:
            print(f"Warning: could not set up debug logging: {exc}", file=sys.__stderr__)
        except Exception:
            pass
        return None


def _setup_desktop_database() -> None:
    r"""Set up SQLite database for desktop (frozen .exe) builds.

    - Creates %LOCALAPPDATA%\TriConveyAgent\ if it doesn't exist
    - Sets CONVEY_DATABASE_URL to use SQLite if frozen and not already set
    - Bootstraps schema on first run (fresh database)
    """
    if not getattr(sys, "frozen", False):
        # Dev mode: use whatever DATABASE_URL is set (default PostgreSQL)
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return

    app_data_dir = Path(local_app_data) / "TriConveyAgent"
    app_data_dir.mkdir(parents=True, exist_ok=True)

    db_path = app_data_dir / "convey.db"

    # Only set DATABASE_URL if not already explicitly configured
    if "CONVEY_DATABASE_URL" not in os.environ:
        os.environ["CONVEY_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"

    # Bootstrap schema if database doesn't exist yet (first run).
    # We check file existence; create_all() is idempotent so it's safe to run
    # on subsequent launches too (it won't drop existing tables).
    first_run = not db_path.exists()
    try:
        asyncio.run(_bootstrap_schema())
    except Exception as e:
        print(f"Warning: failed to bootstrap database schema: {e}", file=sys.stderr)
        return

    # Seed questions catalog on first run
    if first_run:
        try:
            from triconvey_agent.db.bootstrap import seed_questions_if_present
            asyncio.run(seed_questions_if_present())
        except Exception as e:
            print(f"Warning: failed to seed questions: {e}", file=sys.stderr)


async def _bootstrap_schema() -> None:
    """Bootstrap the SQLite database schema on first run.

    Uses SQLAlchemy's metadata.create_all() — NOT the raw schema.sql which
    contains PostgreSQL-specific DDL (TIMESTAMPTZ, INET, pgcrypto) that
    SQLite cannot parse.
    """
    try:
        from triconvey_agent.db.models import ALL_MODELS  # noqa: F401 — ensures all models registered
        from triconvey_agent.db.session import Base, get_engine

        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _apply_desktop_schema_migrations(conn)
        print("Database schema bootstrapped.", file=sys.stderr)
    except Exception as e:
        print(f"Could not bootstrap schema: {e}", file=sys.stderr)


async def _apply_desktop_schema_migrations(conn) -> None:
    """Apply additive schema fixes for long-lived installed SQLite databases."""
    if getattr(conn.dialect, "name", "") != "sqlite":
        return

    result = await conn.exec_driver_sql("PRAGMA table_info(users)")
    user_columns = {str(row[1]) for row in result.fetchall()}

    result = await conn.exec_driver_sql("PRAGMA table_info(runs)")
    run_columns = {str(row[1]) for row in result.fetchall()}

    if "oauth_provider" not in user_columns:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN oauth_provider TEXT")
    if "oauth_subject" not in user_columns:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN oauth_subject TEXT")

    if "progress_pct" not in run_columns:
        await conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN progress_pct FLOAT NOT NULL DEFAULT 0.0")
    if "progress_status" not in run_columns:
        await conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN progress_status TEXT")

    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_oauth "
        "ON users(oauth_provider, oauth_subject) "
        "WHERE oauth_provider IS NOT NULL"
    )


def _check_dependencies() -> None:
    """Warn at startup if optional heavyweight dependencies are missing."""
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                (
                    "pywinauto is not installed \u2014 Auto-fill will not work.\n\n"
                    "To enable Auto-fill, open a terminal and run:\n\n"
                    "    pip install pywinauto\n\n"
                    "Then restart Convey Agent."
                ),
                "Missing Dependency \u2014 Convey Agent",
                0x30,  # MB_ICONWARNING | MB_OK
            )
        except Exception:
            pass


def _ensure_desktop_secrets() -> None:
    """Auto-generate CONVEY_JWT_SECRET on first frozen install if not already set.

    The secret is persisted to %LOCALAPPDATA%\\TriConveyAgent\\.env so it
    survives app updates and restarts. Generating it here (after _load_env)
    ensures it is in os.environ before uvicorn imports security.py, which
    reads the secret at module import time.
    """
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("CONVEY_JWT_SECRET"):
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return

    app_dir = Path(local_app_data) / "TriConveyAgent"
    app_dir.mkdir(parents=True, exist_ok=True)
    env_path = app_dir / ".env"

    # Parse any values already in the file (OPENAI_API_KEY etc.)
    env_values: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_values[key.strip()] = value.strip().strip('"').strip("'")

    # If it was in the file but load_dotenv missed it, just export it.
    if env_values.get("CONVEY_JWT_SECRET"):
        os.environ["CONVEY_JWT_SECRET"] = env_values["CONVEY_JWT_SECRET"]
        return

    # First run: generate a fresh secret and persist it.
    import secrets as _secrets
    env_values["CONVEY_JWT_SECRET"] = _secrets.token_urlsafe(32)
    lines = [f'{k}="{v}"' for k, v in env_values.items() if v]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(str(env_path), 0o600)
    except OSError:
        pass
    os.environ["CONVEY_JWT_SECRET"] = env_values["CONVEY_JWT_SECRET"]


def _load_env() -> None:
    """Load .env from next to the exe (frozen) or from project root (dev)."""
    try:
        from dotenv import load_dotenv

        if getattr(sys, "frozen", False):
            # Installed desktop builds keep durable settings in %LOCALAPPDATA%
            # so updates to Program Files do not wipe API keys or config.
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            if local_app_data:
                env_path = os.path.join(local_app_data, "TriConveyAgent", ".env")
                if os.path.isfile(env_path):
                    load_dotenv(env_path)
                    return
            # Fallback for older portable builds that kept .env next to the exe.
            exe_dir = os.path.dirname(sys.executable)
            env_path = os.path.join(exe_dir, ".env")
            if os.path.isfile(env_path):
                if local_app_data:
                    migrated_path = os.path.join(local_app_data, "TriConveyAgent", ".env")
                    try:
                        Path(migrated_path).parent.mkdir(parents=True, exist_ok=True)
                        if not os.path.isfile(migrated_path):
                            Path(migrated_path).write_text(Path(env_path).read_text(encoding="utf-8"), encoding="utf-8")
                    except Exception:
                        pass
                load_dotenv(env_path)
                return
        # Dev mode: load from project root (default dotenv behaviour)
        load_dotenv()
    except ImportError:
        pass


_PREFERRED_PORT = 8765  # Must match the redirect URI registered in Azure / Google Console.


def _find_open_port() -> int:
    """Try the preferred port first; fall back to any free port if taken."""
    for port in range(_PREFERRED_PORT, _PREFERRED_PORT + 10):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    # Last resort: let the OS pick any free port (OAuth callbacks won't work
    # unless OAUTH_REDIRECT_BASE is manually configured to match).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _DesktopApi:
    """JS-to-Python bridge exposed as window.pywebview.api in the webview.

    Methods here are callable from the frontend via:
        window.pywebview.api.methodName(args)
    """

    def open_browser(self, url: str) -> None:
        """Open a URL in the user's default system browser.

        Used for OAuth flows: pywebview blocks window.open() popups, so we
        open the provider URL in the real browser and poll the backend for
        the result instead of using postMessage.
        """
        import webbrowser
        webbrowser.open(url)

    def _window(self):
        windows = getattr(webview, "windows", [])
        return windows[0] if windows else None

    def pick_triconvey_executable(self) -> str | None:
        """Let non-technical users browse to the Convey desktop executable."""
        window = self._window()
        if window is None:
            return None

        chosen = window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("Executable files (*.exe)", "All files (*.*)"),
        )
        if not chosen:
            return None
        if isinstance(chosen, (list, tuple)):
            return str(chosen[0]) if chosen else None
        return str(chosen)

    def open_local_data_dir(self) -> bool:
        """Open the durable local app-data folder used by the installed app."""
        from triconvey_agent.backend.runtime import ensure_runtime_dirs, get_runtime_paths

        runtime = ensure_runtime_dirs(get_runtime_paths())
        target = runtime.local_app_dir
        if not target.exists():
            return False
        subprocess.Popen(["explorer.exe", str(target)], close_fds=True)
        return True

    def launch_installer(self, installer_path: str) -> bool:
        """Start an Inno Setup installer and let it replace the app in place."""
        path = Path(installer_path)
        if not path.is_file():
            raise FileNotFoundError(f"Installer not found: {installer_path}")

        args = [
            str(path),
            "/SP-",
            "/CLOSEAPPLICATIONS",
            "/FORCECLOSEAPPLICATIONS",
        ]
        subprocess.Popen(args, close_fds=True)
        return True

    def close_app(self) -> None:
        windows = getattr(webview, "windows", [])
        if windows:
            windows[0].destroy()


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Convey Agent backend did not start on {host}:{port}.")


def _run_server(host: str, port: int) -> None:
    uvicorn.run(
        "triconvey_agent.backend.api:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


def _run_ipv6_relay(ipv4_port: int) -> None:
    """Relay TCP from [::1]:{port} → 127.0.0.1:{port}.

    On Windows, 'localhost' often resolves to ::1 (IPv6) rather than 127.0.0.1.
    The OAuth redirect URI uses 'localhost', so without this relay the system
    browser's callback request hits ::1 and gets 'connection refused' because
    uvicorn only binds to 127.0.0.1.  This relay makes both addresses work.
    """
    import asyncio

    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", ipv4_port)
            await asyncio.gather(_pipe(reader, remote_writer), _pipe(remote_reader, writer))
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _serve() -> None:
        try:
            server = await asyncio.start_server(_relay, "::1", ipv4_port)
            async with server:
                await server.serve_forever()
        except Exception:
            pass  # IPv6 unavailable or port taken — silently skip

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve())
    finally:
        loop.close()


def main() -> None:
    debug_log_path = _setup_debug_logging()
    if debug_log_path:
        print(f"[ConveyAgent] Debug log: {debug_log_path}")

    _setup_desktop_database()
    _load_env()
    _ensure_desktop_secrets()
    _check_dependencies()

    host = "127.0.0.1"
    port = _find_open_port()

    # Tell the OAuth module which port we're on BEFORE starting uvicorn.
    # Always override any .env value here: the desktop launcher is the source
    # of truth for the active loopback port, and a stale fixed value can cause
    # the browser callback to complete against a different backend instance.
    #
    # Use `localhost` for the redirect URI even though uvicorn binds to
    # 127.0.0.1. OAuth providers require an exact redirect URI match, and the
    # Azure app registration is configured for localhost.
    os.environ["OAUTH_REDIRECT_BASE"] = f"http://localhost:{port}"

    server_thread = threading.Thread(target=_run_server, args=(host, port), daemon=True)
    server_thread.start()
    # Relay ::1:{port} → 127.0.0.1:{port} so OAuth callbacks work even when
    # Windows resolves 'localhost' to the IPv6 loopback address.
    relay_thread = threading.Thread(target=_run_ipv6_relay, args=(port,), daemon=True)
    relay_thread.start()
    _wait_for_server(host, port)

    webview.create_window(
        "Convey Agent",
        f"http://{host}:{port}/",
        js_api=_DesktopApi(),
        min_size=(1280, 820),
        width=1480,
        height=940,
        text_select=True,
    )
    # Persist WebView2 user data (localStorage, cookies) so login survives app restarts.
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    _webview_data_dir = str(Path(local_app_data) / "TriConveyAgent" / "webview_data") if local_app_data else None
    webview.start(storage_path=_webview_data_dir)


if __name__ == "__main__":
    # freeze_support() MUST be called before anything else in frozen PyInstaller
    # builds that use multiprocessing. Without it, every spawned worker process
    # re-runs main() instead of acting as a worker, creating duplicate windows
    # and log files. This is a no-op in dev mode.
    import multiprocessing
    multiprocessing.freeze_support()
    main()
