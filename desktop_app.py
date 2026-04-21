from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview


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
        print("Database schema bootstrapped.", file=sys.stderr)
    except Exception as e:
        print(f"Could not bootstrap schema: {e}", file=sys.stderr)


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
        log_level="warning",
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
    _setup_desktop_database()
    _load_env()
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
    webview.start()


if __name__ == "__main__":
    main()
