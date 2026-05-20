"""Dynamic fallback layouts.

When no YAML config matches the current foreground, we synthesize a layout
on the fly that lists every visible top-level window of the same process,
each as a button that brings that window to the front via the
focus_window action.
"""
from __future__ import annotations

from typing import Any

import psutil
import win32gui
import win32process


def enum_windows_for_process(pid: int) -> list[tuple[int, str]]:
    """Return (hwnd, title) for every visible, titled top-level window of pid."""
    out: list[tuple[int, str]] = []
    if not pid:
        return out

    def cb(hwnd: int, _: Any) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        # Skip child windows; we only want top-level.
        if win32gui.GetParent(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if wpid == pid:
            out.append((hwnd, title))

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    # Sort by hwnd for stable ordering across polls.
    out.sort(key=lambda t: t[0])
    return out


def enum_windows_for_process_name(process_name: str) -> list[tuple[int, str]]:
    """Like enum_windows_for_process but searches by process name across all pids."""
    if not process_name:
        return []
    target = process_name.lower()
    pids = {p.pid for p in psutil.process_iter(["name"])
            if (p.info.get("name") or "").lower() == target}
    if not pids:
        return []
    out: list[tuple[int, str]] = []

    def cb(hwnd: int, _: Any) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetParent(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if wpid in pids:
            out.append((hwnd, title))

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    out.sort(key=lambda t: t[0])
    return out


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"


def generate_fallback_layout(process_name: str, pid: int) -> dict[str, Any] | None:
    """Build a synthetic layout listing windows of the foreground process.

    Returns None if the process is empty (no foreground / no enumerable windows).
    """
    if not process_name:
        return None

    # Prefer pid-scoped enumeration; if it yields nothing, broaden by process name
    # (covers multi-process apps like Chrome where the foreground pid is one of many).
    windows = enum_windows_for_process(pid) if pid else []
    if not windows:
        windows = enum_windows_for_process_name(process_name)

    canvas_w = 1600
    canvas_h = 1000
    pretty = process_name.rsplit(".", 1)[0].upper()

    widgets: list[dict[str, Any]] = [
        {
            "id": "auto_title",
            "type": "label",
            "x": 40, "y": 24, "w": canvas_w - 80, "h": 20,
            "props": {
                "text": f"AUTO · {pretty} · {len(windows)} WINDOW{'S' if len(windows) != 1 else ''}",
                "align": "left",
                "size": 12,
            },
        }
    ]

    if not windows:
        widgets.append({
            "id": "no_windows",
            "type": "label",
            "x": 40, "y": 64, "w": canvas_w - 80, "h": 40,
            "props": {
                "text": "no visible windows for this process",
                "align": "left",
                "size": 16,
            },
        })
        return _wrap(pretty, widgets, canvas_w, canvas_h)

    # 2-column grid of wide buttons so long titles stay legible.
    cols = 2
    gap = 16
    margin_x = 40
    margin_y = 64
    btn_w = (canvas_w - 2 * margin_x - (cols - 1) * gap) // cols
    btn_h = 96

    for i, (hwnd, title) in enumerate(windows):
        row = i // cols
        col = i % cols
        widgets.append({
            "id": f"win_{hwnd}",
            "type": "button",
            "x": margin_x + col * (btn_w + gap),
            "y": margin_y + row * (btn_h + gap),
            "w": btn_w,
            "h": btn_h,
            "props": {
                "label": _truncate(title, 64),
                "action": {"type": "focus_window", "hwnd": int(hwnd)},
            },
        })

    return _wrap(pretty, widgets, canvas_w, canvas_h)


def _wrap(name: str, widgets: list[dict[str, Any]], w: int, h: int) -> dict[str, Any]:
    return {
        "name": f"Auto: {name}",
        "synthetic": True,
        "canvas": {"width": w, "height": h, "theme": "midnight"},
        "widgets": widgets,
    }


def fingerprint(pid: int, process_name: str) -> tuple:
    """Cheap signature of the current window set; used to detect when the
    auto-layout needs to be rebuilt (e.g., a new window opened)."""
    if pid:
        ws = enum_windows_for_process(pid)
    else:
        ws = enum_windows_for_process_name(process_name)
    return tuple((h, t) for h, t in ws)


def enum_all_visible_windows() -> list[dict[str, Any]]:
    """Return every visible top-level window, grouped with its process name.

    Used by the Apps overlay in the title bar.
    """
    import psutil
    proc_cache: dict[int, str] = {}

    def proc_name(pid: int) -> str:
        if pid in proc_cache:
            return proc_cache[pid]
        try:
            name = psutil.Process(pid).name() if pid else ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = ""
        proc_cache[pid] = name
        return name

    out: list[dict[str, Any]] = []

    def cb(hwnd: int, _: Any) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetParent(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        # Skip cloaked windows (e.g., suspended UWP apps)
        try:
            import ctypes
            cloaked = ctypes.c_int(0)
            DWMWA_CLOAKED = 14
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )
            if cloaked.value:
                return
        except Exception:
            pass
        out.append({
            "hwnd": int(hwnd),
            "title": title,
            "pid": int(pid) if pid else 0,
            "process": proc_name(pid),
        })

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    # Sort by process name then title
    out.sort(key=lambda w: ((w["process"] or "").lower(), w["title"].lower()))
    return out
