"""Brain E -- TriConvey desktop form executor.

Control-finding strategy  (vision_filler approach)
---------------------------------------------------
Primary strategy for ALL control types: label-based proximity search.

  • Find ALL Text elements that fuzzy-match the field label.
  • Score by exact > substring > word-overlap (≥ 2 words).
  • If row_index is set (for repeated labels like "Authority name" on
    each outgoing row), pick the Nth label sorted top-to-bottom.
  • Return the nearest Edit / ComboBox / CheckBox to that label.

Fallback: calibrated absolute-position search (for controls with no
visible Text label or when fuzzy search finds nothing).

Outgoings grid special path
---------------------------
Fields matching "Outgoing N - Authority/Amount/Interest" are handled by
_scan_outgoings_rows(): collects Edit controls in the outgoings Y-band,
groups by Y proximity into rows, assigns authority/amount/interest by
X order (leftmost → authority, middle → amount, rightmost → interest).
If a row doesn't exist yet, Tab is pressed from the last cell to
materialise the next row.

Tab navigation
--------------
click_input() ONLY on TabItem — invoke() fires COM UIA events that
raise -2147220991 on some WPF window states.  All TabItems are
collected via descendants() upfront to avoid stale child_window refs.

Field ID format (from Brain D mapper)
--------------------------------------
    "{tab}::{control_type}::{name_or_nearby_label}::t{top}l{left}"
    separator between top and left is lowercase L (not p).
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from triconvey_agent.canonical.schemas import (
    ActionResult,
    ExecutionReport,
    FormAction,
    FormActionPlan,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pywinauto -- deferred so dry_run=True works without the library installed
# ---------------------------------------------------------------------------

_pywinauto_available = False
try:
    from pywinauto import Application, Desktop
    from pywinauto import mouse as _pw_mouse
    _pywinauto_available = True
except ImportError:
    pass

_pil_available = False
try:
    from PIL import ImageFilter, ImageGrab, ImageOps
    _pil_available = True
except ImportError:
    pass

_ocr_available = False
try:
    import pytesseract

    for _candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
    ):
        _expanded = os.path.expandvars(_candidate)
        if os.path.exists(_expanded):
            pytesseract.pytesseract.tesseract_cmd = _expanded
            break
    _ocr_available = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRICONVEY_EXE_CANDIDATES = [
    r"C:\Program Files\triConvey\triConvey.exe",
    r"C:\Program Files (x86)\triConvey\triConvey.exe",
    r"C:\Program Files\Smokeball\triConvey.exe",
    r"C:\Program Files (x86)\Smokeball\triConvey.exe",
]

SEC32_TAB_ORDER = [
    "Sec. 32 (1)",
    "Sec. 32 (2)",
    "Sec. 32 (3)",
    "Sec. 32 (4)",
    "Sec. 32 (5)",
    "Sec. 32 (6)",
]

TAB_SWITCH_DELAY   = 1.5    # seconds after clicking a tab
WINDOW_OPEN_DELAY  = 3.0    # seconds after opening matter / property window
FIELD_SETTLE_DELAY = 0.15   # seconds after each field write
LAUNCH_POLL        = 2.0    # poll interval while TriConvey loads
LAUNCH_TIMEOUT     = 60     # give up after this many seconds

# How far right/below a label we scan for the matching input control
LABEL_SEARCH_RIGHT = 400    # px right of label right edge
LABEL_SEARCH_BELOW = 55     # px below label top
PROPERTY_DETAILS_TITLE = "Property Details"
MATTER_WINDOW_AUTO_ID = "Matter_Details_Window"


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SimpleRect:
    def __init__(self, left: int, top: int, right: int, bottom: int):
        self.left = int(left)
        self.top = int(top)
        self.right = int(right)
        self.bottom = int(bottom)


# ---------------------------------------------------------------------------
# Field ID parser
# ---------------------------------------------------------------------------
# Format: "{tab}::{ctrl_type}::{name_or_label}::t{top}l{left}"
# The position separator is lowercase L (not p).

_FID_POS_RE = re.compile(r"t(-?\d+)l(-?\d+)$")


def _parse_field_id(field_id: str) -> dict[str, Any]:
    """Parse a Brain D field_id string into components."""
    parts = field_id.split("::", 3)
    result: dict[str, Any] = {
        "tab":          parts[0] if len(parts) > 0 else "",
        "control_type": parts[1] if len(parts) > 1 else "",
        "name":         parts[2] if len(parts) > 2 else "",
        "top":  None,
        "left": None,
    }
    if len(parts) > 3:
        m = _FID_POS_RE.search(parts[3])
        if m:
            result["top"]  = int(m.group(1))
            result["left"] = int(m.group(2))
    return result


# ---------------------------------------------------------------------------
# Focus helper  (matches v4's ensure_focus exactly)
# ---------------------------------------------------------------------------

def _ensure_focus(window) -> None:
    """Bring window to front; restore if minimised."""
    try:
        if window.has_style(0x20000000):   # WS_MINIMIZE
            window.restore()
            time.sleep(0.5)
        window.set_focus()
        time.sleep(0.3)
    except Exception:
        pass


def _safe_window_text(window) -> str:
    try:
        return (window.window_text() or "").strip()
    except Exception:
        return ""


def _find_sec32_tab_strip(window):
    """Return the Tab control that owns the Sec. 32 tab items, if present."""
    try:
        for tab in window.descendants(control_type="Tab"):
            try:
                items = list(tab.descendants(control_type="TabItem"))
            except Exception:
                items = []
            names = {
                _sanitize(item.element_info.name or item.window_text() or "")
                for item in items
            }
            if SEC32_TAB_ORDER[0] in names and SEC32_TAB_ORDER[-1] in names:
                return tab
    except Exception:
        pass
    return None


def _get_property_form_scroll_point(window) -> tuple[int, int]:
    """Pick a safe point inside the active tab body, below the tab strip."""
    wr = window.rectangle()
    x = wr.left + int(wr.width() * 0.65)
    y = wr.top + max(260, int(wr.height() * 0.35))
    try:
        tab_strip = _find_sec32_tab_strip(window)
        if tab_strip:
            tr = tab_strip.rectangle()
            y = max(y, tr.bottom + 140)
    except Exception:
        pass
    y = min(y, wr.bottom - 120)
    return x, y


def _rect_contains(rect, x: int, y: int) -> bool:
    return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom


def _preprocess_image(img):
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def _ocr_data_from_rect(rect: _SimpleRect, psm: int = 6):
    if not (_pil_available and _ocr_available):
        return None, None

    bbox = (rect.left, rect.top, rect.right, rect.bottom)
    img = ImageGrab.grab(bbox=bbox)
    img = _preprocess_image(img)
    data = pytesseract.image_to_data(
        img,
        config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )
    return img, data


def _find_phrase_in_ocr_data(rect: _SimpleRect, data, phrase: str):
    target_words = phrase.lower().split()
    texts = [str(t or "") for t in data.get("text", [])]
    n = len(texts)

    for i in range(n - len(target_words) + 1):
        if not all(target_words[j] in texts[i + j].lower() for j in range(len(target_words))):
            continue

        first_left = data["left"][i]
        first_top = data["top"][i]
        first_h = data["height"][i]
        last_i = i + len(target_words) - 1
        last_right = data["left"][last_i] + data["width"][last_i]

        x1 = rect.left + first_left
        y1 = rect.top + first_top
        x2 = rect.left + last_right
        y2 = y1 + first_h

        return {
            "left": x1,
            "top": y1,
            "right": x2,
            "bottom": y2,
            "center_x": (x1 + x2) // 2,
            "center_y": (y1 + y2) // 2,
        }

    return None


def _get_ocr_rows_from_rect(rect: _SimpleRect, psm: int = 6) -> list[dict[str, Any]]:
    _, data = _ocr_data_from_rect(rect, psm=psm)
    if not data:
        return []

    texts = data.get("text", [])
    lefts = data.get("left", [])
    tops = data.get("top", [])
    widths = data.get("width", [])
    heights = data.get("height", [])

    words = []
    for i, raw in enumerate(texts):
        txt = str(raw or "").strip()
        if not txt:
            continue
        x1 = rect.left + lefts[i]
        y1 = rect.top + tops[i]
        x2 = x1 + widths[i]
        y2 = y1 + heights[i]
        words.append(
            {
                "text": txt,
                "left": x1,
                "top": y1,
                "right": x2,
                "bottom": y2,
                "center_y": (y1 + y2) // 2,
            }
        )

    words.sort(key=lambda w: (w["center_y"], w["left"]))
    rows: list[dict[str, Any]] = []
    tolerance = 14

    for word in words:
        placed = False
        for row in rows:
            if abs(row["center_y"] - word["center_y"]) <= tolerance:
                row["words"].append(word)
                row["left"] = min(row["left"], word["left"])
                row["right"] = max(row["right"], word["right"])
                row["top"] = min(row["top"], word["top"])
                row["bottom"] = max(row["bottom"], word["bottom"])
                row["center_y"] = (row["top"] + row["bottom"]) // 2
                placed = True
                break
        if not placed:
            rows.append(
                {
                    "words": [word],
                    "left": word["left"],
                    "right": word["right"],
                    "top": word["top"],
                    "bottom": word["bottom"],
                    "center_y": word["center_y"],
                }
            )

    final_rows: list[dict[str, Any]] = []
    for row in rows:
        row["words"].sort(key=lambda w: w["left"])
        final_rows.append(
            {
                "label": " ".join(w["text"] for w in row["words"]).strip(),
                "left": row["left"],
                "right": row["right"],
                "top": row["top"],
                "bottom": row["bottom"],
                "center_y": row["center_y"],
            }
        )
    return final_rows


# ---------------------------------------------------------------------------
# Spatial helper -- mirrors v4's _nearest()
# ---------------------------------------------------------------------------

def _nearest(label_rect, candidates):
    """Return the control spatially nearest to and right-of / below label_rect.

    Matches v4's _nearest() logic exactly.
    """
    best, best_dist = None, float("inf")
    for ctrl in candidates:
        try:
            r = ctrl.rectangle()
            if r.width() <= 0:
                continue
            dx = r.left - label_rect.right
            dy = r.top  - label_rect.top
            # To the right on the same row
            if -10 <= dx <= LABEL_SEARCH_RIGHT and abs(dy) <= 30:
                d = abs(dx) + abs(dy)
                if d < best_dist:
                    best_dist, best = d, ctrl
            # Directly below on next line
            if abs(r.left - label_rect.left) < 50 and 0 < dy < LABEL_SEARCH_BELOW:
                d = abs(dy)
                if d < best_dist:
                    best_dist, best = d, ctrl
        except Exception:
            continue
    return best


# ---------------------------------------------------------------------------
# Sanitize helper (from vision_filler)
# ---------------------------------------------------------------------------

def _sanitize(text) -> str:
    """Strip non-printable and Private Use Area Unicode characters."""
    if not text:
        return ""
    return "".join(
        c for c in str(text)
        if c.isprintable() and not ('\uE000' <= c <= '\uF8FF')
    ).strip()


# ---------------------------------------------------------------------------
# Control finders  (vision_filler approach: label-based, fuzzy, row-aware)
# ---------------------------------------------------------------------------

def _fuzzy_find_checkbox(window, name: str):
    """Find a CheckBox by name with fuzzy/partial word-overlap matching.

    Strategy (in order):
    1. Exact name via child_window (fast)
    2. Substring match across all CheckBox/Button descendants
    3. Word-overlap scoring (≥ 2 matching words)
    """
    if not name or name in ("unnamed", ""):
        return None

    name_l = name.lower()
    target_words = set(name_l.split())

    # 1. Exact match — try both CheckBox and Button (WPF alias)
    for ct in ("CheckBox", "Button"):
        try:
            ctrl = window.child_window(title=name, control_type=ct)
            if ctrl.exists(timeout=1):
                return ctrl
        except Exception:
            pass

    # 2. Descend and score
    best, best_score = None, 0
    for ct in ("CheckBox", "Button"):
        try:
            for ctrl in window.descendants(control_type=ct):
                try:
                    n = _sanitize(ctrl.element_info.name or ctrl.window_text() or "")
                    if not n:
                        continue
                    n_l = n.lower()
                    # Exact substring → immediate return
                    if name_l in n_l or n_l in name_l:
                        return ctrl
                    # Word overlap
                    overlap = len(target_words & set(n_l.split()))
                    if overlap > best_score:
                        best_score = overlap
                        best = ctrl
                except Exception:
                    continue
        except Exception:
            continue

    return best if best_score >= 2 else None


def _label_find_edit(window, label: str, row_index: int = None):
    """Find the Edit nearest to a Text label.

    If row_index is given (1-based), find the Nth label match (sorted by Y)
    and return the Edit nearest to it.  This is essential for repeated labels
    like 'Authority name' that appear once per outgoing row.
    """
    if not label or label in ("unnamed", ""):
        return None

    label_l = label.lower()
    target_words = set(label_l.split())

    # ── Step 1: Collect ALL matching Text labels with a score ──
    all_matches = []
    try:
        for t in window.descendants(control_type="Text"):
            try:
                n = _sanitize(t.element_info.name or "")
                if not n:
                    continue
                r = t.rectangle()
                if r.width() <= 0:
                    continue
                n_l = n.lower()
                score = 0
                if n_l == label_l:
                    score = 999
                elif label_l in n_l or n_l in label_l:
                    score = 100 - abs(len(n_l) - len(label_l))
                elif len(target_words) >= 2:
                    overlap = len(target_words & set(n_l.split()))
                    if overlap >= 2:
                        score = overlap * 10
                if score > 0:
                    all_matches.append((score, t, r))
            except Exception:
                continue
    except Exception:
        return None

    if not all_matches:
        return None

    # ── Step 2: Pick label(s) to use ──
    max_score = max(m[0] for m in all_matches)
    good = [m for m in all_matches if m[0] >= max_score * 0.8]

    if row_index is not None:
        # Sort by Y and pick the Nth
        good.sort(key=lambda m: m[2].top)
        if row_index - 1 < len(good):
            _, _, lr = good[row_index - 1]
        else:
            return None
    else:
        good.sort(key=lambda m: -m[0])
        _, _, lr = good[0]

    # ── Step 3: Find nearest Edit to this label ──
    edits = []
    try:
        for e in window.descendants(control_type="Edit"):
            try:
                r = e.rectangle()
                if r.width() <= 0:
                    continue
                dx = r.left - lr.right
                dy = r.top  - lr.top
                # Same row (to the right)
                if -10 <= dx <= LABEL_SEARCH_RIGHT and abs(dy) <= 35:
                    edits.append((abs(dx) + abs(dy), e))
                # Directly below
                elif abs(r.left - lr.left) < 50 and 0 < dy < LABEL_SEARCH_BELOW:
                    edits.append((abs(dy) + abs(r.left - lr.left), e))
            except Exception:
                continue
    except Exception:
        return None

    if edits:
        edits.sort(key=lambda x: x[0])
        return edits[0][1]
    return None


def _label_find_combobox(window, label: str):
    """Find a ComboBox near a Text label with fuzzy matching."""
    if not label or label in ("unnamed", ""):
        return None

    label_l = label.lower()
    target_words = set(label_l.split())

    best_label = None
    best_score = 0
    try:
        for t in window.descendants(control_type="Text"):
            try:
                n = _sanitize(t.element_info.name or "")
                if not n:
                    continue
                r = t.rectangle()
                if r.width() <= 0:
                    continue
                n_l = n.lower()
                if n_l == label_l:
                    best_label = (t, r)
                    break
                if label_l in n_l or n_l in label_l:
                    score = 100 - abs(len(n_l) - len(label_l))
                    if score > best_score:
                        best_score, best_label = score, (t, r)
                elif len(target_words) >= 2:
                    overlap = len(target_words & set(n_l.split()))
                    if overlap >= 2 and overlap > best_score:
                        best_score, best_label = overlap, (t, r)
            except Exception:
                continue
    except Exception:
        return None

    if not best_label:
        return None

    _, lr = best_label
    combos = []
    try:
        for c in window.descendants(control_type="ComboBox"):
            try:
                r = c.rectangle()
                if r.width() <= 0:
                    continue
                dx = r.left - lr.right
                dy = r.top  - lr.top
                if -10 <= dx <= LABEL_SEARCH_RIGHT and abs(dy) <= 35:
                    combos.append((abs(dx) + abs(dy), c))
                elif abs(r.left - lr.left) < 50 and 0 < dy < LABEL_SEARCH_BELOW:
                    combos.append((abs(dy), c))
            except Exception:
                continue
    except Exception:
        return None

    if combos:
        combos.sort(key=lambda x: x[0])
        return combos[0][1]
    return None


# ---------------------------------------------------------------------------
# Outgoings grid scanner (vision_filler approach, but with dynamic columns)
# ---------------------------------------------------------------------------

_OUTGOINGS_COL_NAMES = ("authority", "amount", "interest")


def _is_outgoings_field(label: str):
    """Check if a label refers to an outgoings row.

    Returns (column_name, row_index) e.g. ("authority", 2), or (None, None).
    """
    m = re.search(
        r"outgoing\s+(\d+)\s*-\s*(authority|amount|interest)",
        label.lower(),
    )
    if m:
        return m.group(2), int(m.group(1))
    return None, None


def _outgoings_target_from_action(action: FormAction, fid: dict, row_index: int | None = None):
    """Infer outgoings row/column even when Brain D emits unnamed field labels."""
    qid = (action.question_id or "").lower()
    m = re.search(r"outgoing_(\d+)_(authority|amount|interest)", qid)
    if m:
        return m.group(2), int(m.group(1))

    col, row = _is_outgoings_field(fid.get("name", ""))
    if col and row:
        return col, row

    if fid.get("tab") == SEC32_TAB_ORDER[0] and row_index and fid.get("left") in (-1871, -1449, -1026):
        col_by_left = {-1871: "authority", -1449: "amount", -1026: "interest"}
        return col_by_left.get(fid["left"]), row_index

    return None, None


def _scan_outgoings_rows(window) -> list[dict]:
    """Dynamically scan the outgoings Edit grid.

    Returns a list of row dicts sorted top-to-bottom:
        [{"y": int, "authority": ctrl, "amount": ctrl, "interest": ctrl}, ...]

    Column assignment is by X-order within each row (leftmost=authority,
    middle=amount, rightmost=interest), so it's independent of absolute
    screen coordinates.
    """
    # Find the "Authority name" column header Text to anchor the Y-band
    header_y = None
    header_x_anchor = None
    try:
        for t in window.descendants(control_type="Text"):
            try:
                n = _sanitize(t.element_info.name or "").lower()
                if n in ("authority name", "authority"):
                    r = t.rectangle()
                    if r.width() > 0:
                        header_y = r.top
                        header_x_anchor = r.left
                        break
            except Exception:
                continue
    except Exception:
        pass

    # Collect all Edit controls in the outgoings Y-band
    # If we found the header, scan below it; otherwise scan a wide band
    y_min = (header_y + 10) if header_y is not None else 220
    y_max = y_min + 350

    cells = []
    try:
        for e in window.descendants(control_type="Edit"):
            try:
                r = e.rectangle()
                if r.width() <= 0:
                    continue
                if r.top < y_min or r.top > y_max:
                    continue
                cells.append({"y": r.top, "x": r.left, "w": r.width(), "edit": e})
            except Exception:
                continue
    except Exception:
        return []

    if not cells:
        return []

    # Group cells into rows by Y proximity (10 px tolerance)
    row_buckets: list[dict] = []
    for cell in sorted(cells, key=lambda c: c["y"]):
        placed = False
        for rb in row_buckets:
            if abs(rb["y"] - cell["y"]) <= 10:
                rb["cells"].append(cell)
                rb["y"] = (rb["y"] * (len(rb["cells"]) - 1) + cell["y"]) // len(rb["cells"])
                placed = True
                break
        if not placed:
            row_buckets.append({"y": cell["y"], "cells": [cell]})

    # Map each row's cells to columns by X order
    result: list[dict] = []
    for rb in row_buckets:
        row_cells = sorted(rb["cells"], key=lambda c: c["x"])
        if len(row_cells) < 2:
            continue  # need at least 2 edits for a valid outgoings row
        row_dict: dict = {"y": rb["y"]}
        for i, col_name in enumerate(_OUTGOINGS_COL_NAMES):
            if i < len(row_cells):
                row_dict[col_name] = row_cells[i]["edit"]
        result.append(row_dict)

    return result


def _find_or_create_outgoings_cell(window, column_name: str, row_index: int):
    """Return the Edit cell for (column, row_index).

    If the row doesn't exist yet, press Tab after the last row's last cell
    to create it, then re-scan.
    """
    from pywinauto import keyboard as _kb

    rows = _scan_outgoings_rows(window)
    log.debug("Outgoings grid: %d row(s) found", len(rows))

    if row_index <= len(rows):
        return rows[row_index - 1].get(column_name)

    if not rows:
        log.warning("No outgoings rows visible — cannot create row %d", row_index)
        return None

    # Tab from the last cell to materialise a new row
    last = rows[-1]
    last_cell = (last.get("interest") or last.get("amount") or last.get("authority"))
    if not last_cell:
        return None

    try:
        last_cell.click_input()
        time.sleep(0.3)
        for _ in range(3):
            _kb.send_keys("{TAB}")
            time.sleep(0.3)
        rows = _scan_outgoings_rows(window)
        log.debug("After Tab: %d row(s)", len(rows))
        if row_index <= len(rows):
            return rows[row_index - 1].get(column_name)
    except Exception as exc:
        log.warning("Tab-to-create-row failed: %s", exc)

    return None


def _find_edit_by_position(window, top: int, left: int, tol: int = 25,
                           delta: tuple[int, int] = (0, 0)):
    """Position-based search for Edit fields.

    YAML positions are absolute screen coordinates from scan time.
    delta = (delta_top, delta_left) is computed by _calibrate_position_delta()
    to account for the window having moved between scan and run time.
    """
    if top is None or left is None:
        return None
    adj_top  = top  + delta[0]
    adj_left = left + delta[1]
    best_ctrl, best_dist = None, float("inf")
    try:
        for ctrl in window.descendants(control_type="Edit"):
            try:
                r = ctrl.rectangle()
                if r.width() <= 0:
                    continue
                dt = abs(r.top  - adj_top)
                dl = abs(r.left - adj_left)
                if dt <= tol and dl <= tol:
                    d = dt + dl
                    if d < best_dist:
                        best_dist, best_ctrl = d, ctrl
            except Exception:
                continue
    except Exception:
        return None
    return best_ctrl


def _find_checkbox_by_position(window, top: int, left: int, tol: int = 25,
                                delta: tuple[int, int] = (0, 0)):
    """Position-based search for CheckBox/Button controls (unnamed checkboxes)."""
    if top is None or left is None:
        return None
    adj_top  = top  + delta[0]
    adj_left = left + delta[1]
    best_ctrl, best_dist = None, float("inf")
    for ct in ("CheckBox", "Button"):
        try:
            for ctrl in window.descendants(control_type=ct):
                try:
                    r = ctrl.rectangle()
                    if r.width() <= 0:
                        continue
                    dt = abs(r.top  - adj_top)
                    dl = abs(r.left - adj_left)
                    if dt <= tol and dl <= tol:
                        d = dt + dl
                        if d < best_dist:
                            best_dist, best_ctrl = d, ctrl
                except Exception:
                    continue
        except Exception:
            continue
    return best_ctrl


def _find_combobox_by_position(window, top: int, left: int, tol: int = 25,
                               delta: tuple[int, int] = (0, 0)):
    """Position-based search for ComboBox controls."""
    if top is None or left is None:
        return None
    adj_top = top + delta[0]
    adj_left = left + delta[1]
    best_ctrl, best_dist = None, float("inf")
    try:
        for ctrl in window.descendants(control_type="ComboBox"):
            try:
                r = ctrl.rectangle()
                if r.width() <= 0:
                    continue
                dt = abs(r.top - adj_top)
                dl = abs(r.left - adj_left)
                if dt <= tol and dl <= tol:
                    d = dt + dl
                    if d < best_dist:
                        best_dist, best_ctrl = d, ctrl
            except Exception:
                continue
    except Exception:
        return None
    return best_ctrl


def _find_ctrl(window, fid: dict, delta: tuple[int, int] = (0, 0),
               row_index: int = None) -> Any:
    """Control-finding: vision-filler label approach (primary) + position fallback.

    delta          = (delta_top, delta_left) from coordinate calibration.
    row_index      = 1-based occurrence index for repeated labels (e.g. Authority row 2).

    Strategy order (for all types):
      1. Fuzzy label / proximity search  ← vision_filler approach, robust
      2. Position-based (calibrated)     ← fallback when label is ambiguous/unnamed

    CheckBox  → fuzzy_find_checkbox → position fallback
    Edit      → label_find_edit(row_index) → position fallback
    ComboBox  → label_find_combobox
    """
    ctrl_type = fid["control_type"]
    name      = fid["name"]

    if ctrl_type == "CheckBox":
        # Primary: fuzzy label match
        if name and name not in ("unnamed", ""):
            ctrl = _fuzzy_find_checkbox(window, name)
            if ctrl is not None:
                return ctrl
        # Fallback: calibrated position
        return _find_checkbox_by_position(window, fid["top"], fid["left"], delta=delta)

    if ctrl_type == "Edit":
        # Primary: label-based with row_index (vision_filler approach)
        if name and name not in ("unnamed", ""):
            ctrl = _label_find_edit(window, name, row_index=row_index)
            if ctrl is not None:
                return ctrl
        # Fallback: calibrated position (works even if no Text label is visible)
        if fid["top"] is not None:
            ctrl = _find_edit_by_position(window, fid["top"], fid["left"], delta=delta)
            if ctrl is not None:
                return ctrl

    if ctrl_type == "ComboBox":
        if name and name not in ("unnamed", ""):
            ctrl = _label_find_combobox(window, name)
            if ctrl is not None:
                return ctrl
        if fid["top"] is not None:
            ctrl = _find_combobox_by_position(window, fid["top"], fid["left"], delta=delta)
            if ctrl is not None:
                return ctrl

    return None


# ---------------------------------------------------------------------------
# Action executors  (same as v4)
# ---------------------------------------------------------------------------

def _do_set_text(ctrl, value: str, window) -> None:
    """Set text in an Edit field while avoiding mouse-driven focus changes."""
    _ensure_focus(window)
    try:
        ctrl.set_edit_text(str(value))
        return
    except Exception:
        pass
    try:
        ctrl.type_keys("^a{BACKSPACE}", with_spaces=False)
        time.sleep(0.1)
        ctrl.type_keys(str(value), with_spaces=True, pause=0.02)
        return
    except Exception:
        pass
    # Last resort only.
    ctrl.click_input()
    time.sleep(0.1)
    ctrl.set_edit_text(str(value))


def _settle_window_after_fill(window, *, close_popup: bool = False) -> None:
    """Return focus to the property window without moving the mouse around."""
    try:
        _ensure_focus(window)
    except Exception:
        pass


def _do_set_checkbox(ctrl, value: bool, window) -> None:
    """Set checkbox state: toggle → verify → click_input retry (vision_filler style)."""
    _ensure_focus(window)
    want = bool(value)

    def _get_state():
        try:
            return ctrl.get_toggle_state() == 1
        except Exception:
            try:
                return ctrl.get_check_state() == 1
            except Exception:
                return False

    if _get_state() == want:
        return  # already correct

    # Attempt 1: toggle()
    try:
        ctrl.toggle()
        time.sleep(0.3)
        if _get_state() == want:
            return
    except Exception:
        pass

    # Attempt 2: click_input()
    try:
        _ensure_focus(window)
        ctrl.click_input()
        time.sleep(0.3)
        if _get_state() == want:
            return
    except Exception:
        pass

    # Attempt 3: double-click back if we over-toggled
    try:
        if _get_state() != want:
            ctrl.click_input()
            time.sleep(0.3)
    except Exception:
        pass


def _do_select_dropdown(ctrl, value: str, window) -> None:
    """Select from ComboBox: editable text box → fuzzy list item match."""
    _ensure_focus(window)
    target = str(value).strip()

    # Strategy 1: vision_filler-style editable part first.
    try:
        edit = ctrl.child_window(auto_id="PART_EditableTextBox")
        if edit.exists(timeout=1):
            _ensure_focus(window)
            edit.set_edit_text(target)
            time.sleep(0.3)
            edit.type_keys("{ENTER}", with_spaces=False)
            return
    except Exception:
        pass

    # Strategy 2: ask the ComboBox API directly, which avoids mouse clicks on
    # dropdown rows that appear to destabilize Property Details in Sec. 32 (2).
    try:
        ctrl.select(target)
        time.sleep(0.2)
        return
    except Exception:
        pass

    # Strategy 3: type the zoning code only; many TriConvey combos autocomplete
    # the matching planning zone from the code prefix.
    try:
        edit = ctrl.child_window(auto_id="PART_EditableTextBox")
        if edit.exists(timeout=1):
            code = target.split(" ", 1)[0]
            edit.set_edit_text(code)
            time.sleep(0.2)
            edit.type_keys("{ENTER}", with_spaces=False)
    except Exception:
        pass


def _read_back(ctrl, action: str) -> Any:
    try:
        if action == "set_checkbox":
            try:
                return ctrl.get_toggle_state() == 1
            except Exception:
                return ctrl.get_check_state() == 1
        if action == "select_dropdown":
            try:
                edit = ctrl.child_window(auto_id="PART_EditableTextBox")
                texts = edit.texts()
                if texts and texts[0] and not texts[0].startswith("---"):
                    return texts[0]
            except Exception:
                pass
            try:
                selected = ctrl.selected_text()
                if selected and not str(selected).startswith("---"):
                    return selected
            except Exception:
                pass
            return None
        texts = ctrl.texts()
        return texts[0] if texts else ""
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tab navigation  (vision_filler approach: direct title lookup, no keyboard)
# ---------------------------------------------------------------------------

def _click_tab(window, tab_name: str) -> bool:
    """Switch to a named tab inside the Property Details window.

    Mirrors vision_filler.click_tab() exactly:
      1. child_window(title=tab_name, control_type="TabItem") — fast direct lookup.
      2. Walk all TabItem descendants, compare via _sanitize(element_info.name).

    Uses click_input() ONLY — invoke() fires COM UIA events that raise
    -2147220991 on some WPF window states.

    The old keyboard-arrow fallback (click first tab, arrow N times) is
    intentionally removed: it computed the index in SEC32_TAB_ORDER which
    starts at 0 for "Sec. 32 (1)", so it clicked the ACTUAL first tab
    ("Property Information") and never moved — landing on the wrong tab.
    """
    _ensure_focus(window)
    time.sleep(0.2)

    # Strategy 1: direct child_window lookup (exact title match)
    try:
        tab = window.child_window(title=tab_name, control_type="TabItem")
        if tab.exists(timeout=3):
            tab.click_input()
            time.sleep(TAB_SWITCH_DELAY)
            return True
    except Exception:
        pass

    # Strategy 2: walk all TabItem descendants, compare sanitized name
    try:
        for t in window.descendants(control_type="TabItem"):
            try:
                n = _sanitize(t.element_info.name or t.window_text() or "")
                if n == tab_name:
                    t.click_input()
                    time.sleep(TAB_SWITCH_DELAY)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    log.warning("Tab click failed: %s", tab_name)
    return False


def _tab_is_selected(tab) -> bool | None:
    try:
        return bool(tab.is_selected())
    except Exception:
        pass
    try:
        iface = getattr(tab, "iface_selection_item", None)
        if iface is not None:
            return bool(iface.CurrentIsSelected)
    except Exception:
        pass
    try:
        legacy = tab.get_properties().get("legacy_properties", {})
        state = str(legacy.get("State", "")).lower()
        if state:
            return "selected" in state
    except Exception:
        pass
    return None


def _get_active_sec32_tab(window) -> str | None:
    tab_strip = _find_sec32_tab_strip(window)
    if tab_strip is None:
        return None
    try:
        for item in tab_strip.descendants(control_type="TabItem"):
            try:
                if _tab_is_selected(item):
                    return _sanitize(item.element_info.name or item.window_text() or "")
            except Exception:
                continue
    except Exception:
        pass
    return None


def _activate_sec32_tab(window, tab_name: str, retries: int = 3) -> bool:
    """Click the requested Sec. 32 tab and verify it stayed selected."""
    for _ in range(max(retries, 1)):
        _ensure_focus(window)
        if not _click_tab(window, tab_name):
            continue
        active = _get_active_sec32_tab(window)
        if active is None or active == tab_name:
            return True
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------------------
# TriConveyAgent  (closely mirrors v4's class structure)
# ---------------------------------------------------------------------------

class TriConveyAgent:
    """Connects to TriConvey, opens the matter, fills Sec. 32 tabs."""

    def __init__(self, triconvey_exe: str | None = None):
        self.exe = triconvey_exe or self._find_exe()
        self.app             = None
        self.main_window     = None
        self.matter_window   = None
        self.property_window = None
        # Offset between YAML scan-time absolute coords and current screen coords.
        # Computed by _calibrate_position_delta() after Property Details opens.
        self._coord_delta: tuple[int, int] = (0, 0)

    def _log(self, msg: str) -> None:
        print(f"  {msg}")
        log.info(msg)

    def _find_exe(self) -> str | None:
        for p in TRICONVEY_EXE_CANDIDATES:
            if os.path.exists(p):
                return p
        return None

    def _maximize_matter_window(self, window) -> None:
        try:
            window.maximize()
            time.sleep(1.0)
        except Exception:
            pass
        _ensure_focus(window)
        time.sleep(0.8)

    def _normalize_matter_window(self, window) -> None:
        self._maximize_matter_window(window)

    def _get_search_results_rect(self, window) -> _SimpleRect:
        rect = window.rectangle()
        return _SimpleRect(
            rect.left + 340,
            rect.top + 410,
            rect.right - 120,
            rect.bottom - 90,
        )

    def _is_result_header_or_noise(self, label: str) -> bool:
        text = label.lower().strip()
        if not text:
            return True
        header_markers = (
            "matter number",
            "client",
            "other party",
            "description",
            "state",
            "stage",
            "staff",
            "tags",
            "show",
            "open, pending",
            "search results for",
        )
        noise_markers = ("leads", "matters", "contacts", "events", "tasks", "memos", "documents")
        return any(marker in text for marker in header_markers) or text in noise_markers

    def _choose_first_result_row(self, rows: list[dict[str, Any]]):
        candidates = []
        for row in rows:
            label = row["label"].strip()
            lower = label.lower()
            if self._is_result_header_or_noise(lower):
                continue
            score = 0
            if any(ch.isdigit() for ch in label):
                score += 2
            if any(word in lower for word in ("sale", "purchase", "transfer", "subdivision")):
                score += 2
            if any(state in lower for state in ("vic", "nsw", "qld", "wa")):
                score += 1
            if len(label) > 12:
                score += 1
            candidates.append((score, row))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1]["center_y"]))
        return candidates[0][1]

    def _click_first_result_by_ocr(self, window) -> bool:
        if not (_pil_available and _ocr_available):
            self._log("  OCR route unavailable for matter search results.")
            return False
        _ensure_focus(window)
        rect = self._get_search_results_rect(window)
        rows = _get_ocr_rows_from_rect(rect, psm=6)
        if not rows:
            self._log("  No OCR rows found in search results.")
            return False
        best_row = self._choose_first_result_row(rows)
        if not best_row:
            self._log("  Could not identify a valid search result row.")
            return False
        click_x = max(rect.left + 20, best_row["left"] + 30)
        click_y = best_row["center_y"]
        self._log(f"  OCR selected result row: {best_row['label']}")
        try:
            _pw_mouse.click(button="left", coords=(click_x, click_y))
            time.sleep(0.4)
            _pw_mouse.double_click(coords=(click_x, click_y))
            time.sleep(2.5)
            return True
        except Exception as exc:
            self._log(f"  OCR result click failed: {exc}")
            return False

    def _connect_to_matter_window(self, timeout: int = 20) -> bool:
        self._log("  Looking for matter window ...")
        end = time.time() + timeout
        while time.time() < end:
            for w in Desktop(backend="uia").windows():
                try:
                    title = _safe_window_text(w)
                    if not title:
                        continue
                    if title.lower() == PROPERTY_DETAILS_TITLE.lower():
                        continue

                    info = w.element_info
                    if getattr(info, "automation_id", "") == MATTER_WINDOW_AUTO_ID:
                        self.app = Application(backend="uia").connect(handle=w.handle)
                        self.matter_window = self.app.window(handle=w.handle)
                        self._log(f"Matter window: {title}")
                        return True

                    app = Application(backend="uia").connect(handle=w.handle)
                    win = app.window(handle=w.handle)
                    try:
                        if win.child_window(auto_id="LbItems").exists(timeout=0.3):
                            self.app = app
                            self.matter_window = win
                            self._log(f"Matter window: {title}")
                            return True
                    except Exception:
                        pass
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def _get_matter_details_content_rects(self, window) -> list[_SimpleRect]:
        rect = window.rectangle()
        height = rect.bottom - rect.top
        return [
            _SimpleRect(
                rect.left + 10,
                rect.top + 220,
                rect.right - 320,
                rect.top + int(height * 0.78),
            ),
            _SimpleRect(
                rect.left + 10,
                rect.top + 260,
                rect.right - 470,
                rect.top + int(height * 0.70),
            ),
            _SimpleRect(
                rect.left + 40,
                rect.top + 180,
                rect.right - 260,
                rect.top + int(height * 0.82),
            ),
        ]

    def _find_property_details_row_rect(self, window):
        if not (_pil_available and _ocr_available):
            return None

        _ensure_focus(window)
        full_rect = window.rectangle()
        window_rect = _SimpleRect(full_rect.left, full_rect.top, full_rect.right, full_rect.bottom)
        full_rows = _get_ocr_rows_from_rect(window_rect, psm=6)
        for row in full_rows:
            if "property details" in row["label"].lower():
                return {
                    "left": max(window_rect.left + 8, row["left"] - 20),
                    "right": min(window_rect.right - 8, row["right"] + 120),
                    "top": max(window_rect.top, row["top"] - 12),
                    "bottom": min(window_rect.bottom, row["bottom"] + 14),
                    "center_y": row["center_y"],
                }

        expected_markers = (
            "property details",
            "conveyancing details",
            "nomination details",
            "vendor",
            "matter type",
            "purchaser",
        )

        for content_rect in self._get_matter_details_content_rects(window):
            _, data = _ocr_data_from_rect(content_rect, psm=6)
            if not data:
                continue

            texts = " ".join(str(t or "") for t in data.get("text", [])).lower()
            if not any(marker in texts for marker in expected_markers):
                continue

            hit = _find_phrase_in_ocr_data(content_rect, data, "Property Details")
            if not hit:
                continue

            row_top = max(content_rect.top, hit["top"] - 12)
            row_bottom = min(content_rect.bottom, hit["bottom"] + 14)
            return {
                "left": content_rect.left + 8,
                "right": content_rect.right - 8,
                "top": row_top,
                "bottom": row_bottom,
                "center_y": (row_top + row_bottom) // 2,
            }

        return None

    def _try_open_property_details_via_matter_details(self, window) -> bool:
        if not (_pil_available and _ocr_available):
            self._log("  OCR route unavailable — Pillow/Tesseract not installed.")
            return False

        self._log("  Trying Matter Details pane route ...")
        self._normalize_matter_window(window)
        row_rect = self._find_property_details_row_rect(window)
        if not row_rect:
            self._log("  Matter Details pane OCR did not find the Property Details row.")
            return False

        x = row_rect["left"] + 60
        y = row_rect["center_y"]
        self._log(f"  Property Details row found near ({x}, {y})")

        try:
            _pw_mouse.click(button="left", coords=(x, y))
            time.sleep(0.5)
            if self._wait_property_window(timeout=3):
                return True

            _pw_mouse.double_click(coords=(x, y))
            time.sleep(1.0)
            return self._wait_property_window(timeout=6)
        except Exception as exc:
            self._log(f"  Matter Details OCR route failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Coordinate calibration
    # ------------------------------------------------------------------

    def _calibrate_position_delta(self, window) -> None:
        """Infer the offset between YAML scan coords and current screen coords.

        The YAML field positions were recorded as absolute screen coordinates
        when TriConvey was on a particular monitor.  If the window has since
        moved (e.g. from extended display to primary), all position lookups
        will be wrong by a constant offset.

        Fix: find one Edit by its known label, compare its actual absolute
        position to the YAML position → delta = actual - yaml.
        Apply that delta to every subsequent position lookup.

        Known anchors (label, yaml_top, yaml_left) from Tab 1 YAML:
            "Authority"             282  -1871
            "Amount"                282  -1449
            "Their total does not exceed"   226  -1300
        """
        # (label_text, yaml_top, yaml_left)
        anchors = [
            ("Authority",                 282, -1871),
            ("Amount",                    282, -1449),
            ("Their total does not exceed", 226, -1300),
            ("Company Name",              794, -1774),
            ("Policy No.",                727, -1571),
        ]
        for label, yaml_top, yaml_left in anchors:
            ctrl = _label_find_edit(window, label)
            if ctrl is None:
                continue
            try:
                r = ctrl.rectangle()
                if r.width() <= 0:
                    continue
                delta = (r.top - yaml_top, r.left - yaml_left)
                self._coord_delta = delta
                self._log(
                    f"  Coord calibrated via '{label}': "
                    f"delta_top={delta[0]:+d} delta_left={delta[1]:+d}"
                )
                return
            except Exception:
                continue
        self._log(
            "  WARNING: Could not calibrate coordinates. "
            "Position matching will use YAML absolute coords directly."
        )

    # ------------------------------------------------------------------
    # Step 1: launch or connect  (matches v4 exactly)
    # ------------------------------------------------------------------

    def launch_or_connect(self) -> bool:
        self._log("Checking if TriConvey is running ...")

        # Try main triConvey window
        try:
            self.app = Application(backend="uia").connect(title="triConvey", timeout=3)
            self.main_window = self.app.window(title="triConvey")
            if self.main_window.exists(timeout=2):
                self._log("TriConvey is running.")
                return True
        except Exception:
            pass

        # Check for open matter windows (Sale / Purchase) with MatterDetailsRibbonTab
        try:
            desktop = Desktop(backend="uia")
            for w in desktop.windows():
                title = w.window_text()
                if " - Sale" in title or " - Purchase" in title:
                    try:
                        app = Application(backend="uia").connect(handle=w.handle)
                        win = app.window(handle=w.handle)
                        if win.child_window(auto_id="MatterDetailsRibbonTab").exists(timeout=1):
                            self.app = app
                            self.matter_window = win
                            self._log(f"Matter already open: {title}")
                            return True
                    except Exception:
                        continue
        except Exception:
            pass

        # Not running -- launch
        if not self.exe:
            self._log("ERROR: TriConvey not found. Use --exe to specify path.")
            return False

        self._log(f"Launching TriConvey: {self.exe}")
        try:
            subprocess.Popen([self.exe])
        except Exception as exc:
            self._log(f"ERROR launching: {exc}")
            return False

        elapsed = 0.0
        while elapsed < LAUNCH_TIMEOUT:
            time.sleep(LAUNCH_POLL)
            elapsed += LAUNCH_POLL
            try:
                self.app = Application(backend="uia").connect(title="triConvey", timeout=2)
                self.main_window = self.app.window(title="triConvey")
                if self.main_window.exists(timeout=2):
                    self._log(f"TriConvey launched ({elapsed:.0f}s).")
                    time.sleep(3)
                    return True
            except Exception:
                if int(elapsed) % 10 == 0:
                    self._log(f"  Still loading ... ({elapsed:.0f}s)")

        self._log("ERROR: Timed out waiting for TriConvey.")
        return False

    # ------------------------------------------------------------------
    # Step 2: find and open matter  (v4 strategy)
    # ------------------------------------------------------------------

    def find_and_open_matter(self, client_name: str) -> bool:
        # Already open? Check if a matter window is already showing
        for win in (self.matter_window, self.main_window):
            if win:
                try:
                    title = win.window_text().lower()
                    if " - sale" in title or " - purchase" in title:
                        self.matter_window = win
                        self._log(f"Matter already open: {win.window_text()}")
                        return True
                except Exception:
                    pass

        window = self.main_window or self.matter_window
        if not window:
            self._log("ERROR: No TriConvey window.")
            return False

        _ensure_focus(window)

        # Matters tab
        self._log("Navigating to Matters ...")
        try:
            tab = window.child_window(auto_id="MainView_Left_Docked_Tab_Matters_TabItem")
            if tab.exists(timeout=3):
                tab.click_input()
                time.sleep(1)
        except Exception:
            pass

        # Search box -- set_edit_text (no stray keystrokes)
        self._log(f"Searching: {client_name}")
        try:
            search = window.child_window(auto_id="TbTheTextBox", control_type="Edit")
            if not search.exists(timeout=5):
                self._log("Search box not found.")
            else:
                search.click_input()
                time.sleep(0.5)
                search.set_edit_text(client_name)
                time.sleep(0.5)
                _ensure_focus(window)
                search.click_input()
                time.sleep(0.2)
                search.type_keys("{ENTER}", with_spaces=False)
                time.sleep(4)
                self._log("  Search submitted.")
        except Exception as exc:
            self._log(f"  Search error: {exc}")

        if self._click_first_result_by_ocr(window):
            if self._connect_to_matter_window(timeout=20):
                return True
            self._log("  OCR opened a row, but the matter window was not detected yet.")

        return self._click_matter(client_name)

    def _click_matter(self, client_name: str) -> bool:
        """Open the top search result in TriConvey's matter list.

        The search box already filtered the results — we just click the first
        visible row without any name comparison.  This is completely generic
        regardless of how TriConvey formats or orders names.
        """
        window = self.main_window or self.matter_window

        def _is_matter_row(text: str) -> bool:
            """A matter row typically contains ' - Sale' or ' - Purchase'."""
            tl = text.lower()
            return " - sale" in tl or " - purchase" in tl

        # Strategy 1: DataGrid — click the first visible row
        self._log("  Checking data grid ...")
        try:
            grid = window.child_window(auto_id="MatterSummaryDataGrid")
            if grid.exists(timeout=3):
                # Collect rows from any supported control type
                rows: list = []
                for ct in ("DataItem", "ListItem", "TreeItem", "Custom"):
                    rows = list(grid.descendants(control_type=ct))
                    if rows:
                        break
                if not rows:
                    rows = list(grid.children())

                if rows:
                    self._log(f"  Found {len(rows)} row(s) — opening first.")
                    _ensure_focus(window)
                    rows[0].double_click_input()
                    time.sleep(3)
                    return self._detect_matter(client_name)

                # Grid visible but no item controls — click inside it
                rect = grid.rectangle()
                if rect.width() > 50 and rect.height() > 30:
                    cx = rect.left + rect.width() // 2
                    cy = rect.top + 30
                    self._log(f"  Grid area click ({cx}, {cy})")
                    _ensure_focus(window)
                    _pw_mouse.double_click(coords=(cx, cy))
                    time.sleep(3)
                    return self._detect_matter(client_name)
        except Exception as exc:
            self._log(f"  Grid: {exc}")

        # Strategy 2: first ListItem anywhere in the window
        self._log("  Checking ListItem descendants ...")
        try:
            for li in window.descendants(control_type="ListItem"):
                try:
                    r = li.rectangle()
                    if r.width() > 20 and r.height() > 5:
                        self._log(f"  Found ListItem: {(li.window_text() or '')[:60]}")
                        _ensure_focus(window)
                        li.double_click_input()
                        time.sleep(3)
                        return self._detect_matter(client_name)
                except Exception:
                    continue
        except Exception:
            pass

        # Strategy 3: first Text element that looks like a matter title
        self._log("  Scanning for matter title text ...")
        try:
            for t in window.descendants(control_type="Text"):
                try:
                    txt = t.element_info.name or t.window_text() or ""
                    if _is_matter_row(txt):
                        r = t.rectangle()
                        if r.width() > 20:
                            self._log(f"  Found: {txt[:60]}")
                            _ensure_focus(window)
                            parent = t.parent()
                            try:
                                (parent or t).double_click_input()
                            except Exception:
                                t.double_click_input()
                            time.sleep(3)
                            return self._detect_matter(client_name)
                except Exception:
                    continue
        except Exception:
            pass

        # Manual fallback
        self._log("Could not open matter automatically.")
        self._log("Please double-click the matter row, then press Enter.")
        input("  Press Enter when matter is open ...")
        return self._detect_matter(client_name)

    def _detect_matter(self, _client_name: str = "") -> bool:
        """Find the matter window that opened after a double-click.

        A matter window always has ' - Sale' or ' - Purchase' in its title.
        We pick the first such window that isn't the main triConvey shell.
        """
        time.sleep(2)

        for w in Desktop(backend="uia").windows():
            try:
                title = w.window_text()
                tl = title.lower()
                if (" - sale" in tl or " - purchase" in tl) and tl != "triconvey":
                    self.app = Application(backend="uia").connect(handle=w.handle)
                    self.matter_window = self.app.window(handle=w.handle)
                    self._log(f"Matter window: {title}")
                    return True
            except Exception:
                continue

        self._log("Matter window not found — using main window as fallback.")
        self.matter_window = self.main_window
        return True

    # ------------------------------------------------------------------
    # Step 3: open Property Details  (v4 strategy)
    # ------------------------------------------------------------------

    def open_property_details(self) -> bool:
        self._log("Opening Property Details ...")
        window = self.matter_window or self.main_window
        _ensure_focus(window)
        time.sleep(1)

        # Preferred route: Matter Details window → Property Details row → Sec. 32 tabs.
        if self._try_open_property_details_via_matter_details(window):
            return True

        def _try_open(ctrl) -> bool:
            """Double-click a control; fall back to its parent."""
            _ensure_focus(window)
            try:
                r = ctrl.rectangle()
                cx = r.left + max(r.width() // 2, 5)
                cy = r.top  + max(r.height() // 2, 5)
                _pw_mouse.click(button='left', coords=(cx, cy))
                time.sleep(0.3)
                _pw_mouse.double_click(coords=(cx, cy))
            except Exception:
                try:
                    ctrl.double_click_input()
                except Exception:
                    return False
            time.sleep(3.0)
            return self._wait_property_window()

        # Strategy 1: LbItems sidebar  (use descendants — WPF virtualises children)
        try:
            lb = window.child_window(auto_id="LbItems")
            if lb.exists(timeout=5):
                self._log("  Found LbItems sidebar.")

                # Click the sidebar to force virtualised items to render
                try:
                    lr = lb.rectangle()
                    _pw_mouse.click(button='left', coords=(lr.left + 10, lr.top + 10))
                    time.sleep(0.5)
                except Exception:
                    pass

                # Scan descendants (ListItem, Text) for "Property Details"
                for desc in lb.descendants():
                    try:
                        n = (desc.element_info.name or desc.window_text() or "").lower()
                        if "property details" in n and len(n) < 60:
                            self._log(f"  Found: {n!r}")
                            return _try_open(desc)
                    except Exception:
                        continue

                # Scroll down inside the sidebar if not found yet
                self._log("  Scrolling LbItems to find Property Details ...")
                try:
                    lb_r = lb.rectangle()
                    scroll_x = lb_r.left + lb_r.width() // 2
                    scroll_y = lb_r.top + lb_r.height() // 2
                    for _ in range(8):
                        _pw_mouse.scroll(coords=(scroll_x, scroll_y), wheel_dist=-3)
                        time.sleep(0.4)
                        for desc in lb.descendants():
                            try:
                                n = (desc.element_info.name or desc.window_text() or "").lower()
                                if "property details" in n and len(n) < 60:
                                    self._log(f"  Found after scroll: {n!r}")
                                    return _try_open(desc)
                            except Exception:
                                continue
                except Exception:
                    pass
        except Exception as exc:
            self._log(f"  LbItems: {exc}")

        # Strategy 2: broad text search across the whole matter window
        self._log("  Broad search for 'Property Details' text ...")
        try:
            candidates = []
            for t in window.descendants(control_type="Text"):
                try:
                    n = t.element_info.name or t.window_text() or ""
                    if "property details" in n.lower() and len(n) < 60:
                        r = t.rectangle()
                        if r.width() > 20:
                            candidates.append((r.top, t))
                except Exception:
                    continue
            # Sort by top position — leftmost sidebar item first
            for _, t in sorted(candidates):
                self._log(f"  Trying text element: {t.element_info.name!r}")
                if _try_open(t):
                    return True
        except Exception:
            pass

        # Strategy 3: ListItem / NavigationViewItem descendants
        self._log("  Checking ListItem descendants ...")
        try:
            for li in window.descendants(control_type="ListItem"):
                try:
                    n = (li.element_info.name or li.window_text() or "").lower()
                    if "property details" in n:
                        self._log(f"  Found ListItem: {n!r}")
                        return _try_open(li)
                except Exception:
                    continue
        except Exception:
            pass

        # Manual fallback
        self._log("Could not auto-open Property Details.")
        self._log("Please open Property Details from the Matter Details window, then press Enter.")
        input("  Press Enter when Property Details is open ...")
        return self._wait_property_window()

    def _wait_property_window(self, timeout: int = 20) -> bool:
        self._log("  Waiting for Property Details window ...")
        for _ in range(timeout):
            for w in Desktop(backend="uia").windows():
                try:
                    title = w.window_text()
                    if "property details" in title.lower():
                        self.app = Application(backend="uia").connect(handle=w.handle)
                        self.property_window = self.app.window(handle=w.handle)
                        # Keep non-fullscreen
                        try:
                            if self.property_window.is_maximized():
                                self.property_window.restore()
                                time.sleep(0.4)
                        except Exception:
                            pass
                        self._log(f"  Property Details: {title}")
                        time.sleep(2)   # let Sec. 32 form render fully
                        return True
                except Exception:
                    continue
            time.sleep(1)
        self._log("ERROR: Property Details window not found.")
        return False

    # ------------------------------------------------------------------
    # Step 4: fill Sec. 32 tabs
    # ------------------------------------------------------------------

    def _refresh_property_window(self) -> bool:
        """Reconnect to Property Details window if the UIA element tree was rebuilt.

        After many fill operations, WPF can rebuild its element tree, making
        the old window reference stale.  Re-scan the Desktop to get a fresh handle.
        """
        try:
            for w in Desktop(backend="uia").windows():
                try:
                    if "property details" in w.window_text().lower():
                        self.app = Application(backend="uia").connect(handle=w.handle)
                        self.property_window = self.app.window(handle=w.handle)
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _scroll_to_top(self, window) -> None:
        """Scroll the form content pane back to the top.

        WPF uses a VirtualizingStackPanel inside a ScrollViewer — controls
        outside the visible viewport are not created in the UIA tree, so
        descendants() genuinely cannot see them.  Scrolling to the top
        before starting each tab ensures the first fields are accessible.
        """
        try:
            # Scroll relative to the current cursor position to avoid cursor jumps.
            for _ in range(3):
                _pw_mouse.scroll(wheel_dist=18)
                time.sleep(0.15)
        except Exception:
            try:
                window.type_keys("{PGUP}")
                time.sleep(0.3)
            except Exception:
                pass

    def _scroll_down(self, window, notches: int = 5) -> None:
        """Scroll the form content down by `notches` wheel clicks."""
        try:
            _pw_mouse.scroll(wheel_dist=-notches)
            time.sleep(0.3)
        except Exception:
            pass

    @staticmethod
    def _compute_row_indices(tab_actions: list[FormAction]) -> dict[str, int]:
        """Pre-compute row_index for repeated Edit labels within the same tab.

        For labels like 'Authority name' that appear in multiple outgoing rows,
        we sort all actions with that label by their YAML top position and assign
        row_index 1, 2, 3 … in that order.

        Returns: {field_id: row_index}  (only for labels that repeat ≥ 2 times)
        """
        from collections import defaultdict as _dd
        label_groups: dict[str, list[tuple[int, str]]] = _dd(list)
        for action in tab_actions:
            fid = _parse_field_id(action.field_id)
            if (
                fid["control_type"] == "Edit"
                and fid["name"]
                and fid["name"] != "unnamed"
                and fid["top"] is not None
            ):
                label_groups[fid["name"].lower()].append((fid["top"], action.field_id))

        row_indices: dict[str, int] = {}
        for _lbl, entries in label_groups.items():
            if len(entries) >= 2:
                entries.sort(key=lambda x: x[0])
                for i, (_top, fid_str) in enumerate(entries):
                    row_indices[fid_str] = i + 1
        return row_indices

    @staticmethod
    def _outgoings_sort_key(action: FormAction) -> tuple:
        """Sort outgoings fields by (row, column) so row 1 fills before row 2."""
        fid = _parse_field_id(action.field_id)
        col, row = _outgoings_target_from_action(action, fid)
        if col and row:
            col_order = {"authority": 0, "amount": 1, "interest": 2}.get(col, 9)
            return (0, row, col_order)   # outgoings first
        return (1, 0, 0)                 # non-outgoings after

    def fill_sec32_tabs(self, actions: list[FormAction], *, dry_run: bool = False) -> list[ActionResult]:
        """Navigate Sec. 32 (1)-(6) in order and fill every action.

        Control-finding strategy
        ------------------------
        Primary:  vision-filler label approach (fuzzy Text label search +
                  nearest Edit/ComboBox/CheckBox).  Works regardless of
                  whether the window has moved since YAML was captured.
        Fallback: calibrated-position search (for unnamed controls or when
                  no label is visible in the current viewport).

        Scroll strategy
        ---------------
        WPF's VirtualizingStackPanel only renders controls that are inside
        the visible viewport.  Scroll to top at the start of each tab, then
        scroll down and retry (up to MAX_SCROLL_ATTEMPTS × 5 notches) for
        any control not found in the current view.
        """
        if not self.property_window and not dry_run:
            self._log("ERROR: Property Details window not open.")
            return [ActionResult(action=a, status="failed", error="no property window")
                    for a in actions]

        tab_groups: dict[str, list[FormAction]] = defaultdict(list)
        filtered_actions: list[FormAction] = []
        results: list[ActionResult] = []
        for action in actions:
            fid = _parse_field_id(action.field_id)
            if fid["tab"] not in SEC32_TAB_ORDER:
                results.append(
                    ActionResult(
                        action=action,
                        status="skipped",
                        error=f"Skipped non-Sec32 tab: {fid['tab']}",
                    )
                )
                continue
            filtered_actions.append(action)
            tab_groups[fid["tab"]].append(action)

        if not filtered_actions:
            return results

        # Pre-compute row_indices per tab (for repeated Edit labels)
        row_indices_by_tab: dict[str, dict[str, int]] = {}
        for tab_name, tab_acts in tab_groups.items():
            row_indices_by_tab[tab_name] = self._compute_row_indices(tab_acts)

        window = self.property_window
        current_tab: str | None = None
        failed_tabs: set[str] = set()

        if not dry_run:
            # ── Click Tab 1 first so the form fully renders, THEN calibrate ──
            # Calibration sets delta for the position-based fallback finder.
            # With label-based finding as primary, calibration failure is
            # non-fatal — fields will still be found by label proximity.
            self._log("  Navigating to Sec. 32 (1) to render form ...")
            if _activate_sec32_tab(window, SEC32_TAB_ORDER[0]):
                self._log("  Tab 'Sec. 32 (1)' active — calibrating positions ...")
                self._scroll_to_top(window)
                self._calibrate_position_delta(window)
            else:
                self._log(
                    "  WARNING: Could not switch to Sec. 32 (1) for calibration. "
                    "Label-based finding will still work; position fallback may be "
                    "inaccurate if the window has moved."
                )

        # Build ordered list: process tabs in Sec32 order;
        # within each tab put outgoings first (sorted by row/column)
        ordered_actions: list[FormAction] = []
        for sec32_tab in SEC32_TAB_ORDER:
            tab_acts = tab_groups.get(sec32_tab, [])
            # Sort: outgoings by (row, col), then everything else
            tab_acts_sorted = sorted(tab_acts, key=self._outgoings_sort_key)
            ordered_actions.extend(tab_acts_sorted)

        for action in ordered_actions:
            fid = _parse_field_id(action.field_id)
            tab_name = fid["tab"]

            if tab_name != current_tab:
                current_tab = tab_name
                n = len(tab_groups[tab_name])
                self._log(f"\n  Tab: {tab_name} ({n} field(s))")

                if not dry_run:
                    if not self._refresh_property_window():
                        self._log("  WARN: Could not refresh window ref — using existing.")
                    window = self.property_window

                    if not _activate_sec32_tab(window, tab_name):
                        self._log(f"  WARN: Could not navigate to {tab_name} — skipping.")
                        failed_tabs.add(tab_name)
                    else:
                        self._scroll_to_top(window)

            if tab_name in failed_tabs:
                results.append(ActionResult(
                    action=action, status="failed",
                    error=f"Could not navigate to tab: {tab_name}",
                ))
                continue

            row_index = row_indices_by_tab[tab_name].get(action.field_id)
            result = self._execute_one(window, action, fid=fid,
                                       dry_run=dry_run, row_index=row_index)
            results.append(result)

        return results

    def _execute_one(self, window, action: FormAction, *, fid: dict, dry_run: bool,
                     row_index: int = None) -> ActionResult:
        if action.action == "skip":
            return ActionResult(action=action, status="skipped")
        if action.needs_review_first:
            return ActionResult(action=action, status="pending_review")

        name = fid["name"]
        if name in ("unnamed", ""):
            name = (f"t{fid['top']}l{fid['left']}" if fid["top"] is not None else "?")
        short = (name[:55] + "...") if len(name) > 55 else name

        if dry_run:
            self._log(f"    [DRY-RUN] {action.action:<14} {short}")
            return ActionResult(action=action, status="skipped", error="(dry-run)")

        # ── Outgoings grid: special path via X-order column scanner ──────────
        col, row = _outgoings_target_from_action(action, fid, row_index=row_index)
        if col and row and fid["control_type"] == "Edit":
            ctrl = _find_or_create_outgoings_cell(window, col, row)
            if ctrl is None:
                # Scroll and try once more
                self._scroll_to_top(window)
                ctrl = _find_or_create_outgoings_cell(window, col, row)
            if ctrl is None:
                self._log(f"    NOT FOUND (outgoings row {row} col {col}): {short}")
                return ActionResult(
                    action=action, status="failed",
                    error=f"Outgoings cell not found: row={row} col={col}",
                )
            try:
                _do_set_text(ctrl, action.payload or "", window)
                vshort = str(action.payload or "")[:40]
                self._log(f"    set_text  {short!r:50} = {vshort!r}  [outgoing r{row} {col}]")
            except Exception as exc:
                self._log(f"    ERROR on {short}: {exc}")
                return ActionResult(action=action, status="failed", error=str(exc))
            time.sleep(FIELD_SETTLE_DELAY)
            return ActionResult(action=action, status="filled")

        # ── Standard path: label-based find + position fallback ─────────────
        MAX_SCROLL_ATTEMPTS = 8
        ctrl = _find_ctrl(window, fid, delta=self._coord_delta, row_index=row_index)

        if ctrl is None:
            for scroll_n in range(1, MAX_SCROLL_ATTEMPTS + 1):
                self._scroll_down(window, notches=5)
                ctrl = _find_ctrl(window, fid, delta=self._coord_delta, row_index=row_index)
                if ctrl is not None:
                    self._log(f"    (found after {scroll_n} scroll(s))")
                    break

        # ── Lazy delta calibration from first Edit found ──────────────────────
        if ctrl is not None and self._coord_delta == (0, 0) \
                and fid["control_type"] == "Edit" and fid["top"] is not None:
            try:
                r = ctrl.rectangle()
                if r.width() > 0:
                    self._coord_delta = (r.top - fid["top"], r.left - fid["left"])
                    self._log(
                        f"    Lazy-calibrated delta={self._coord_delta} "
                        f"from {fid['name']!r}"
                    )
            except Exception:
                pass

        if ctrl is None:
            pos_str = ""
            if fid["top"] is not None:
                adj_top  = fid["top"]  + self._coord_delta[0]
                adj_left = fid["left"] + self._coord_delta[1]
                pos_str = (
                    f" [yaml={fid['top']},{fid['left']};"
                    f" adj={adj_top},{adj_left};"
                    f" delta={self._coord_delta}]"
                )
            ri_str = f" row_index={row_index}" if row_index else ""
            self._log(f"    NOT FOUND: {short}{pos_str}{ri_str}")
            return ActionResult(
                action=action, status="failed",
                error=f"Control not found: {action.field_id}",
            )

        try:
            if action.action == "set_text":
                _do_set_text(ctrl, action.payload or "", window)
                self._log(f"    set_text  {short!r:50} = {str(action.payload)[:50]!r}")
            elif action.action == "set_checkbox":
                _do_set_checkbox(ctrl, bool(action.payload), window)
                self._log(f"    checkbox  {short!r:50} = {action.payload}")
            elif action.action == "select_dropdown":
                _do_select_dropdown(ctrl, str(action.payload or ""), window)
                self._log(f"    dropdown  {short!r:50} = {str(action.payload)[:50]!r}")
            else:
                return ActionResult(action=action, status="skipped",
                                    error=f"Unknown: {action.action}")
        except Exception as exc:
            self._log(f"    ERROR on {short}: {exc}")
            return ActionResult(action=action, status="failed", error=str(exc))

        time.sleep(FIELD_SETTLE_DELAY)

        actual = _read_back(ctrl, action.action)
        if action.expected_after is not None and actual != action.expected_after:
            self._log(
                f"    MISMATCH {short}: "
                f"expected {action.expected_after!r} got {actual!r}"
            )
            return ActionResult(
                action=action, status="failed", actual_value=actual,
                error=f"Verify: expected {action.expected_after!r} got {actual!r}",
            )

        return ActionResult(action=action, status="filled", actual_value=actual)


# ---------------------------------------------------------------------------
# Review gate
# ---------------------------------------------------------------------------

def _default_review_prompt(review_items: list[FormAction]) -> bool:
    print("\n" + "=" * 60)
    print("REVIEW GATE -- these fields need human review:")
    print("=" * 60)
    for item in review_items:
        fid = _parse_field_id(item.field_id)
        print(f"  [{item.question_id}]  {fid['tab']} > {fid['name'] or fid.get('top', '?')}")
    print()
    answer = input("Proceed with auto-fill for NON-review fields? [y/N] ").strip().lower()
    return answer == "y"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute_action_plan(
    plan: FormActionPlan,
    *,
    client_name: str = "",
    dry_run: bool = False,
    triconvey_exe: str | None = None,
    review_gate_callback=None,
) -> ExecutionReport:
    """Execute a FormActionPlan against the live TriConvey window.

    Parameters
    ----------
    plan                  : FormActionPlan from Brain D.
    client_name           : Full client name to search in TriConvey.
    dry_run               : Log actions but make zero UI changes.
    triconvey_exe         : Override TriConvey executable path.
    review_gate_callback  : callable(list[FormAction]) -> bool.
    """
    if not dry_run and not _pywinauto_available:
        raise RuntimeError(
            "pywinauto is not installed.\n"
            "Install with:  pip install pywinauto\n"
            "Preview with:  --dry-run"
        )

    report = ExecutionReport(started_at=datetime.utcnow())

    auto_actions   = [a for a in plan.actions if a.action != "skip" and not a.needs_review_first]
    review_actions = [a for a in plan.actions if a.needs_review_first]
    skip_actions   = [a for a in plan.actions if a.action == "skip"]

    for action in skip_actions:
        report.results.append(ActionResult(action=action, status="skipped"))

    # Review gate
    if plan.review_gate_required and review_actions:
        cb = review_gate_callback or _default_review_prompt
        if not cb(review_actions):
            for action in auto_actions + review_actions:
                report.results.append(ActionResult(
                    action=action, status="skipped", error="aborted at review gate"))
            _finalise(report)
            return report

    for action in review_actions:
        report.results.append(ActionResult(action=action, status="pending_review"))

    if not auto_actions:
        _finalise(report)
        return report

    # Banner
    print()
    print("=" * 60)
    print("  Brain E --", "DRY-RUN preview" if dry_run else "TriConvey Form Executor")
    if client_name:
        print(f"  Client:  {client_name}")
    print(f"  Actions: {len(auto_actions)} auto | {len(review_actions)} review | {len(skip_actions)} skip")
    print("=" * 60)

    agent = TriConveyAgent(triconvey_exe=triconvey_exe)

    if not dry_run:
        if not agent.launch_or_connect():
            err = "Could not connect to TriConvey."
            for a in auto_actions:
                report.results.append(ActionResult(action=a, status="failed", error=err))
            _finalise(report)
            raise RuntimeError(err)

        if client_name:
            if not agent.find_and_open_matter(client_name):
                err = f"Could not open matter for '{client_name}'."
                for a in auto_actions:
                    report.results.append(ActionResult(action=a, status="failed", error=err))
                _finalise(report)
                raise RuntimeError(err)
            time.sleep(2)

        if not agent.open_property_details():
            err = "Could not open Property Details."
            for a in auto_actions:
                report.results.append(ActionResult(action=a, status="failed", error=err))
            _finalise(report)
            raise RuntimeError(err)

        time.sleep(1)

    fill_results = agent.fill_sec32_tabs(auto_actions, dry_run=dry_run)
    report.results.extend(fill_results)
    _finalise(report)
    return report


def _finalise(report: ExecutionReport) -> None:
    report.completed_at         = datetime.utcnow()
    report.total_filled         = sum(1 for r in report.results if r.status == "filled")
    report.total_verified       = sum(1 for r in report.results if r.status == "verified")
    report.total_failed         = sum(1 for r in report.results if r.status == "failed")
    report.total_skipped        = sum(1 for r in report.results if r.status == "skipped")
    report.total_pending_review = sum(1 for r in report.results if r.status == "pending_review")
