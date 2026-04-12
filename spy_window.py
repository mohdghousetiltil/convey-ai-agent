"""Quick spy: print all UI controls in the foreground/target window.

Usage:
    python spy_window.py                   # auto-finds Property Details
    python spy_window.py "My Window Title" # target a specific window title (substring)
    python spy_window.py --list            # just list all open windows

Redirect output:  python spy_window.py > spy.txt 2>&1
"""
from __future__ import annotations

import sys
import time

try:
    from pywinauto import Application, Desktop
except ImportError:
    print("ERROR: pip install pywinauto")
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if "--list" in args:
        print("Open windows:")
        for w in Desktop(backend="uia").windows():
            try:
                t = w.window_text()
                if t:
                    print(f"  {t!r}")
            except Exception:
                pass
        return

    target = args[0] if args else "property details"
    target_lower = target.lower()

    found = None
    for w in Desktop(backend="uia").windows():
        try:
            if target_lower in w.window_text().lower():
                found = w
                print(f"Target window: {w.window_text()!r}")
                break
        except Exception:
            continue

    if not found:
        print(f"No window matching {target!r}. Listing all:")
        for w in Desktop(backend="uia").windows():
            try:
                t = w.window_text()
                if t:
                    r = w.rectangle()
                    print(f"  [{t}]  top={r.top} left={r.left}")
            except Exception:
                pass
        return

    app = Application(backend="uia").connect(handle=found.handle)
    win = app.window(handle=found.handle)

    print(f"Window rect: {win.rectangle()}")

    all_ctrl = list(win.descendants())
    print(f"Total descendants: {len(all_ctrl)}\n")

    for c in all_ctrl:
        try:
            ct   = c.element_info.control_type or ""
            name = c.window_text() or c.element_info.name or ""
            aid  = c.element_info.automation_id or ""
            r    = c.rectangle()
            print(f"{ct:<22} name={name!r:<55} auto_id={aid!r:<30} top={r.top:<6} left={r.left:<6} w={r.width():<5} h={r.height()}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
