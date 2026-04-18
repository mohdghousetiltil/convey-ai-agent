#!/usr/bin/env python
"""Mouse and window behaviour trace for TriConvey fills.

Usage:
    python diagnose_mouse_behavior.py
    python diagnose_mouse_behavior.py --interval-ms 50 --duration 120
    python diagnose_mouse_behavior.py --output output/mouse_trace.jsonl

What it does:
1. Polls the cursor position at a fixed interval.
2. Tracks the foreground window title/handle.
3. Tracks whether the Property Details window exists and its rectangle.
4. Prints only meaningful events: cursor jumps, foreground changes,
   Property Details appearing/disappearing, and periodic heartbeats.
5. Writes the full trace to JSONL for later review.

Suggested workflow:
1. Start this script in one terminal.
2. Run the normal TriConvey fill in another terminal.
3. Stop this script with Ctrl+C after the failure.
4. Share the console output and the JSONL trace.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from pywinauto import Desktop
except ImportError:
    print("ERROR: pywinauto not installed. Install with: pip install pywinauto")
    sys.exit(1)


USER32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


@dataclass
class RectInfo:
    left: int
    top: int
    right: int
    bottom: int


@dataclass
class TraceEvent:
    t: float
    kind: str
    cursor_x: int
    cursor_y: int
    foreground_title: str
    foreground_handle: int
    property_exists: bool
    property_title: str | None
    property_handle: int | None
    property_rect: RectInfo | None
    note: str | None = None
    distance: float | None = None


def get_cursor_pos() -> tuple[int, int]:
    pt = POINT()
    USER32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def get_foreground_handle() -> int:
    return int(USER32.GetForegroundWindow())


def get_window_title_from_handle(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = USER32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    USER32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value or ""


def rect_from_window(window) -> RectInfo | None:
    try:
        r = window.rectangle()
        return RectInfo(left=r.left, top=r.top, right=r.right, bottom=r.bottom)
    except Exception:
        return None


def find_property_details_window():
    for w in Desktop(backend="uia").windows():
        try:
            title = w.window_text() or ""
            if "property details" in title.lower():
                return w
        except Exception:
            continue
    return None


def distance(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def emit(event: TraceEvent, handle):
    printable = {
        "t": round(event.t, 3),
        "kind": event.kind,
        "cursor": [event.cursor_x, event.cursor_y],
        "foreground": event.foreground_title or "<none>",
        "property_exists": event.property_exists,
        "note": event.note,
        "distance": round(event.distance, 1) if event.distance is not None else None,
    }
    print(json.dumps(printable, ensure_ascii=True))
    handle.write(json.dumps(asdict(event), ensure_ascii=True) + "\n")
    handle.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace mouse movement and Property Details window behaviour.")
    parser.add_argument("--interval-ms", type=int, default=50, help="Polling interval in milliseconds. Default: 50")
    parser.add_argument("--duration", type=int, default=0, help="Optional max duration in seconds. Default: run until Ctrl+C")
    parser.add_argument(
        "--jump-threshold",
        type=int,
        default=120,
        help="Minimum cursor distance in pixels to log as a jump. Default: 120",
    )
    parser.add_argument(
        "--heartbeat",
        type=int,
        default=5,
        help="Emit a heartbeat event every N seconds even if nothing changed. Default: 5",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "mouse_trace.jsonl",
        help="JSONL output path. Default: output/mouse_trace.jsonl",
    )
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    interval = max(args.interval_ms, 10) / 1000.0
    started = time.time()
    deadline = started + args.duration if args.duration > 0 else None
    last_heartbeat = 0.0

    print("Mouse behaviour trace started. Press Ctrl+C to stop.")
    print(f"Writing full trace to: {args.output.resolve()}")

    last_cursor = None
    last_foreground = None
    last_property_handle = None
    last_property_exists = None

    with args.output.open("w", encoding="utf-8") as handle:
        try:
            while True:
                now = time.time()
                if deadline and now >= deadline:
                    break

                cursor = get_cursor_pos()
                fg_handle = get_foreground_handle()
                fg_title = get_window_title_from_handle(fg_handle)

                prop = find_property_details_window()
                prop_exists = prop is not None
                prop_title = None
                prop_handle = None
                prop_rect = None
                if prop is not None:
                    try:
                        prop_title = prop.window_text() or ""
                        prop_handle = int(prop.handle)
                        prop_rect = rect_from_window(prop)
                    except Exception:
                        pass

                if last_property_exists is None:
                    emit(
                        TraceEvent(
                            t=now - started,
                            kind="start",
                            cursor_x=cursor[0],
                            cursor_y=cursor[1],
                            foreground_title=fg_title,
                            foreground_handle=fg_handle,
                            property_exists=prop_exists,
                            property_title=prop_title,
                            property_handle=prop_handle,
                            property_rect=prop_rect,
                            note="initial state",
                        ),
                        handle,
                    )

                if prop_exists != last_property_exists:
                    emit(
                        TraceEvent(
                            t=now - started,
                            kind="property_window",
                            cursor_x=cursor[0],
                            cursor_y=cursor[1],
                            foreground_title=fg_title,
                            foreground_handle=fg_handle,
                            property_exists=prop_exists,
                            property_title=prop_title,
                            property_handle=prop_handle,
                            property_rect=prop_rect,
                            note="appeared" if prop_exists else "disappeared",
                        ),
                        handle,
                    )

                if prop_handle != last_property_handle and prop_exists:
                    emit(
                        TraceEvent(
                            t=now - started,
                            kind="property_handle",
                            cursor_x=cursor[0],
                            cursor_y=cursor[1],
                            foreground_title=fg_title,
                            foreground_handle=fg_handle,
                            property_exists=prop_exists,
                            property_title=prop_title,
                            property_handle=prop_handle,
                            property_rect=prop_rect,
                            note="handle changed",
                        ),
                        handle,
                    )

                if fg_handle != last_foreground:
                    emit(
                        TraceEvent(
                            t=now - started,
                            kind="foreground",
                            cursor_x=cursor[0],
                            cursor_y=cursor[1],
                            foreground_title=fg_title,
                            foreground_handle=fg_handle,
                            property_exists=prop_exists,
                            property_title=prop_title,
                            property_handle=prop_handle,
                            property_rect=prop_rect,
                            note="foreground window changed",
                        ),
                        handle,
                    )

                if last_cursor is not None:
                    jump = distance(cursor, last_cursor)
                    if jump >= args.jump_threshold:
                        note = "large cursor jump"
                        if prop_rect is not None:
                            if cursor[0] < prop_rect.left or cursor[0] > prop_rect.right or cursor[1] < prop_rect.top or cursor[1] > prop_rect.bottom:
                                note = "large cursor jump outside Property Details"
                        emit(
                            TraceEvent(
                                t=now - started,
                                kind="cursor_jump",
                                cursor_x=cursor[0],
                                cursor_y=cursor[1],
                                foreground_title=fg_title,
                                foreground_handle=fg_handle,
                                property_exists=prop_exists,
                                property_title=prop_title,
                                property_handle=prop_handle,
                                property_rect=prop_rect,
                                note=note,
                                distance=jump,
                            ),
                            handle,
                        )

                if now - last_heartbeat >= args.heartbeat:
                    emit(
                        TraceEvent(
                            t=now - started,
                            kind="heartbeat",
                            cursor_x=cursor[0],
                            cursor_y=cursor[1],
                            foreground_title=fg_title,
                            foreground_handle=fg_handle,
                            property_exists=prop_exists,
                            property_title=prop_title,
                            property_handle=prop_handle,
                            property_rect=prop_rect,
                            note="heartbeat",
                        ),
                        handle,
                    )
                    last_heartbeat = now

                last_cursor = cursor
                last_foreground = fg_handle
                last_property_exists = prop_exists
                last_property_handle = prop_handle
                time.sleep(interval)

        except KeyboardInterrupt:
            now = time.time()
            cursor = get_cursor_pos()
            fg_handle = get_foreground_handle()
            fg_title = get_window_title_from_handle(fg_handle)
            prop = find_property_details_window()
            prop_exists = prop is not None
            prop_title = prop.window_text() if prop is not None else None
            prop_handle = int(prop.handle) if prop is not None else None
            prop_rect = rect_from_window(prop) if prop is not None else None
            emit(
                TraceEvent(
                    t=now - started,
                    kind="stop",
                    cursor_x=cursor[0],
                    cursor_y=cursor[1],
                    foreground_title=fg_title,
                    foreground_handle=fg_handle,
                    property_exists=prop_exists,
                    property_title=prop_title,
                    property_handle=prop_handle,
                    property_rect=prop_rect,
                    note="stopped by user",
                ),
                handle,
            )
            print("Trace stopped.")


if __name__ == "__main__":
    main()
