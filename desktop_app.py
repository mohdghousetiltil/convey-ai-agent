from __future__ import annotations

import os
import socket
import sys
import threading
import time

import uvicorn
import webview


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
            # Frozen .exe: look for .env next to the executable
            exe_dir = os.path.dirname(sys.executable)
            env_path = os.path.join(exe_dir, ".env")
            if os.path.isfile(env_path):
                load_dotenv(env_path)
                return
            # Also check user data dir
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            if local_app_data:
                env_path = os.path.join(local_app_data, "TriConveyAgent", ".env")
                if os.path.isfile(env_path):
                    load_dotenv(env_path)
                    return
        # Dev mode: load from project root (default dotenv behaviour)
        load_dotenv()
    except ImportError:
        pass


def _find_open_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def main() -> None:
    _load_env()
    _check_dependencies()

    host = "127.0.0.1"
    port = _find_open_port()

    server_thread = threading.Thread(target=_run_server, args=(host, port), daemon=True)
    server_thread.start()
    _wait_for_server(host, port)

    webview.create_window(
        "Convey Agent",
        f"http://{host}:{port}/",
        min_size=(1280, 820),
        width=1480,
        height=940,
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
