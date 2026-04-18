"""Brain E diagnostics -- run this while Property Details is open.

Usage:
    python diagnose_triconvey.py

It will:
1. Find the Property Details window
2. Print every control it can see (type, name, auto_id, rect)
3. Print all Text labels with their rectangles
4. Print all CheckBox names
5. Print all TabItem titles
6. For each Sec.32 tab, click it and dump what's inside

Run: python diagnose_triconvey.py > diag_output.txt 2>&1
Then share diag_output.txt so we can fix the field-finding logic.
"""
from __future__ import annotations

import sys
import time

try:
    from pywinauto import Application, Desktop
    from pywinauto import mouse as _pw_mouse
except ImportError:
    print("ERROR: pywinauto not installed.  pip install pywinauto")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rect_str(ctrl) -> str:
    try:
        r = ctrl.rectangle()
        return f"top={r.top} left={r.left} w={r.width()} h={r.height()}"
    except Exception:
        return "rect=?"


def _name(ctrl) -> str:
    try:
        return ctrl.window_text() or ctrl.element_info.name or ""
    except Exception:
        return ""


def _auto_id(ctrl) -> str:
    try:
        return ctrl.element_info.automation_id or ""
    except Exception:
        return ""


def _ctrl_type(ctrl) -> str:
    try:
        return ctrl.element_info.control_type or ""
    except Exception:
        return ""


def dump_window(window, title: str, max_controls: int = 300) -> None:
    print(f"\n{'=' * 70}")
    print(f"  WINDOW: {title}")
    print(f"  Rect:   {_rect_str(window)}")
    print(f"{'=' * 70}")

    try:
        all_ctrls = list(window.descendants())
    except Exception as e:
        print(f"  ERROR getting descendants: {e}")
        return

    print(f"  Total descendants: {len(all_ctrls)}")

    # Group by control type
    by_type: dict[str, list] = {}
    for c in all_ctrls:
        ct = _ctrl_type(c)
        by_type.setdefault(ct, []).append(c)

    print("\n--- Control type counts ---")
    for ct, ctrls in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"  {ct:<25} x {len(ctrls)}")

    # TabItem titles
    if "TabItem" in by_type:
        print("\n--- TabItems ---")
        for c in by_type["TabItem"]:
            print(f"  [{_name(c)!r:40}]  auto_id={_auto_id(c)!r}  {_rect_str(c)}")

    # CheckBox names
    for ct in ("CheckBox", "Button"):
        if ct in by_type:
            candidates = [c for c in by_type[ct] if _name(c)]
            if candidates:
                print(f"\n--- {ct} with names ({len(candidates)}) ---")
                for c in candidates[:80]:
                    print(f"  [{_name(c)!r:55}]  auto_id={_auto_id(c)!r}  {_rect_str(c)}")

    # Text labels (non-empty, reasonable width)
    if "Text" in by_type:
        labels = [c for c in by_type["Text"] if _name(c) and c.rectangle().width() > 10]
        print(f"\n--- Text labels ({len(labels)}) ---")
        for c in labels[:120]:
            print(f"  [{_name(c)!r:55}]  {_rect_str(c)}")

    # Edit fields
    if "Edit" in by_type:
        edits = by_type["Edit"]
        print(f"\n--- Edit fields ({len(edits)}) ---")
        for c in edits[:80]:
            try:
                val = c.window_text()
            except Exception:
                val = "?"
            print(f"  name={_name(c)!r:30} auto_id={_auto_id(c)!r:30} val={val!r:30} {_rect_str(c)}")

    # ComboBox fields
    if "ComboBox" in by_type:
        combos = by_type["ComboBox"]
        print(f"\n--- ComboBox fields ({len(combos)}) ---")
        for c in combos[:40]:
            print(f"  name={_name(c)!r:30} auto_id={_auto_id(c)!r:30} {_rect_str(c)}")

    # All controls raw dump (truncated)
    print(f"\n--- Raw dump (first {max_controls}) ---")
    for c in all_ctrls[:max_controls]:
        ct = _ctrl_type(c)
        print(f"  {ct:<20} name={_name(c)!r:50} auto_id={_auto_id(c)!r:30} {_rect_str(c)}")


def click_tab(window, tab_name: str) -> bool:
    print(f"\n>>> Clicking tab: {tab_name!r}")
    try:
        t = window.child_window(title=tab_name, control_type="TabItem")
        if t.exists(timeout=3):
            t.click_input()
            time.sleep(2)
            return True
    except Exception as e:
        print(f"  child_window approach failed: {e}")

    for desc in window.descendants(control_type="TabItem"):
        try:
            if desc.window_text() == tab_name:
                desc.click_input()
                time.sleep(2)
                return True
        except Exception:
            continue

    print(f"  Tab not found: {tab_name}")
    return False


def dump_tab_contents(window, tab_name: str) -> None:
    """Click the tab, then print a focused summary of its controls."""
    if not click_tab(window, tab_name):
        return

    print(f"\n  --- Contents of {tab_name} ---")

    # Text labels visible after tab switch
    labels = []
    for c in window.descendants(control_type="Text"):
        n = _name(c)
        if n and c.rectangle().width() > 10:
            labels.append((c.rectangle().top, c.rectangle().left, n, c.rectangle()))
    labels.sort()
    print(f"  Text labels ({len(labels)}):")
    for top, left, n, r in labels[:60]:
        print(f"    [{n!r:60}]  top={top} left={left}")

    # Edit fields
    edits = list(window.descendants(control_type="Edit"))
    print(f"  Edit fields ({len(edits)}):")
    for c in edits[:40]:
        try:
            val = c.window_text()
        except Exception:
            val = ""
        r = c.rectangle()
        print(f"    name={_name(c)!r:30} auto_id={_auto_id(c)!r:25} val={val!r:20} top={r.top} left={r.left}")

    # CheckBox fields
    for ct in ("CheckBox", "Button"):
        cbs = [c for c in window.descendants(control_type=ct) if _name(c)]
        if cbs:
            print(f"  {ct}es ({len(cbs)}):")
            for c in cbs[:30]:
                try:
                    state = c.get_toggle_state()
                except Exception:
                    try:
                        state = c.get_check_state()
                    except Exception:
                        state = "?"
                r = c.rectangle()
                print(f"    [{_name(c)!r:55}]  state={state}  top={r.top} left={r.left}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Triconvey Brain E -- Control Diagnostics")
    print("Searching for Property Details window ...")

    # Find Property Details window
    prop_window = None
    prop_title  = ""
    for w in Desktop(backend="uia").windows():
        try:
            title = w.window_text()
            if "property details" in title.lower():
                prop_window = w
                prop_title  = title
                break
        except Exception:
            continue

    if prop_window is None:
        print("\nProperty Details window NOT FOUND.")
        print("Listing all visible windows instead:")
        for w in Desktop(backend="uia").windows():
            try:
                title = w.window_text()
                if title:
                    r = w.rectangle()
                    print(f"  [{title!r:60}]  {_rect_str(w)}")
            except Exception:
                pass
        print("\nPlease open Property Details in TriConvey then re-run.")
        return

    print(f"Found: {prop_title!r}")

    # Connect properly
    app = Application(backend="uia").connect(handle=prop_window.handle)
    window = app.window(handle=prop_window.handle)

    # Full dump
    dump_window(window, prop_title)

    # Per-tab dumps
    tabs = [
        "Sec. 32 (1)",
        "Sec. 32 (2)",
        "Sec. 32 (3)",
        "Sec. 32 (4)",
        "Sec. 32 (5)",
        "Sec. 32 (6)",
    ]

    print("\n\n" + "=" * 70)
    print("  PER-TAB CONTENT DUMP")
    print("=" * 70)

    for tab in tabs:
        dump_tab_contents(window, tab)

    print("\n\nDiagnostics complete.")


if __name__ == "__main__":
    main()
