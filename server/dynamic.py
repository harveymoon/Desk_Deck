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


def enum_all_visible_windows(include_hidden: bool = False, with_icons: bool = True) -> list[dict[str, Any]]:
    """Return every visible top-level window, grouped with its process name.

    Used by the Apps overlay in the title bar and by the editor's Hidden Apps
    modal (which passes include_hidden=True to see filtered apps too).
    """
    import psutil
    from . import filters, icons

    proc_cache: dict[int, tuple[str, str]] = {}  # pid -> (name, exe_path)

    def proc_info(pid: int) -> tuple[str, str]:
        if pid in proc_cache:
            return proc_cache[pid]
        name, exe = "", ""
        try:
            p = psutil.Process(pid)
            name = p.name() or ""
            try:
                exe = p.exe() or ""
            except (psutil.AccessDenied, OSError):
                exe = ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        proc_cache[pid] = (name, exe)
        return name, exe

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
            win_class = win32gui.GetClassName(hwnd) or ""
        except Exception:
            win_class = ""
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
        process, exe = proc_info(pid)
        hidden = filters.is_hidden(process, title, win_class)
        if hidden and not include_hidden:
            return
        entry: dict[str, Any] = {
            "hwnd": int(hwnd),
            "title": title,
            "pid": int(pid) if pid else 0,
            "process": process,
            "win_class": win_class,
            "hidden": hidden,
        }
        if with_icons and exe:
            entry["icon"] = icons.get_icon_data_url(exe)
        out.append(entry)

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    out.sort(key=lambda w: ((w["process"] or "").lower(), w["title"].lower()))
    return out


def expand_window_list(widget: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    """Replace a window_list widget with a grid of focus/activate buttons.

    Source values:
      - 'process_windows' (default): visible top-level windows of the active process
      - 'chrome_tabs': open Chrome tabs via DevTools Protocol (port 9222)
    """
    from . import chrome

    props = widget.get("props") or {}
    source = (props.get("source") or "process_windows").lower()
    cols = max(1, int(props.get("columns") or 2))
    gap = int(props.get("gap") or 12)
    btn_h = int(props.get("button_height") or 90)
    rows_limit = int(props.get("max_rows") or 0)  # 0 = no limit; just keep stacking

    x0 = int(widget.get("x", 0))
    y0 = int(widget.get("y", 0))
    total_w = int(widget.get("w", 800))
    btn_w = max(40, (total_w - (cols - 1) * gap) // cols)

    items = _list_items(source, context)
    if rows_limit:
        items = items[: cols * rows_limit]

    out: list[dict[str, Any]] = []
    # If empty, show a small label inside the widget's bounds so the user knows why.
    if not items:
        out.append({
            "id": (widget.get("id") or "wl") + "_empty",
            "type": "label",
            "x": x0, "y": y0, "w": total_w, "h": 24,
            "props": {
                "text": _empty_message(source),
                "align": "left", "size": 14,
            },
        })
        return out

    base_id = widget.get("id") or "wl"
    for i, item in enumerate(items):
        row = i // cols
        col = i % cols
        out.append({
            "id": f"{base_id}_{item['key']}",
            "type": "button",
            "x": x0 + col * (btn_w + gap),
            "y": y0 + row * (btn_h + gap),
            "w": btn_w,
            "h": btn_h,
            "props": {
                "label": item["label"],
                "action": item["action"],
            },
        })
    return out


def _list_items(source: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    from . import chrome
    if source == "chrome_tabs":
        if chrome.available():
            return [
                {
                    "key": "tab_" + (t.get("id") or "")[:10],
                    "label": (t.get("title") or "(untitled)")[:60],
                    "action": {"type": "chrome_tab", "tab_id": t.get("id") or ""},
                }
                for t in chrome.list_tabs()
            ]
        # CDP not enabled — fall back to Chrome's OS windows. Each Chrome window
        # title is "<active tab title> - Google Chrome", which is still useful.
        wins = enum_windows_for_process_name("chrome.exe")
        return [
            {
                "key": f"hwnd{hwnd}",
                "label": (title or "(untitled)")[:60],
                "action": {"type": "focus_window", "hwnd": int(hwnd)},
            }
            for hwnd, title in wins
        ]

    # process_windows (default)
    pid = int(context.get("pid") or 0)
    process = context.get("process") or ""
    wins = enum_windows_for_process(pid) if pid else []
    if not wins and process:
        wins = enum_windows_for_process_name(process)
    return [
        {
            "key": f"hwnd{hwnd}",
            "label": (title or "(untitled)")[:60],
            "action": {"type": "focus_window", "hwnd": int(hwnd)},
        }
        for hwnd, title in wins
    ]


def _empty_message(source: str) -> str:
    if source == "chrome_tabs":
        return "chrome not running"
    return "no windows"


def window_list_fingerprint(cfg: dict[str, Any] | None, context: dict[str, Any]) -> tuple:
    """Hashable signature of every window_list widget's contents in cfg.
    Used by the poll loop to detect changes (tab opened, window closed) and
    trigger a re-broadcast."""
    if not cfg:
        return ()
    sigs: list[tuple] = []
    for w in cfg.get("widgets") or []:
        if w.get("type") != "window_list":
            continue
        items = _list_items((w.get("props") or {}).get("source", "process_windows"), context)
        sigs.append((w.get("id"), tuple((it["key"], it["label"]) for it in items)))
    return tuple(sigs)


def enum_distinct_processes(include_hidden: bool = True) -> list[dict[str, Any]]:
    """Distinct processes with at least one visible top-level window, with icons.

    Used by the editor's Hidden Apps modal so the user can tick processes to
    hide. Returns one entry per process name, dedup'd, with a representative
    icon and a sample window title.
    """
    wins = enum_all_visible_windows(include_hidden=include_hidden, with_icons=True)
    seen: dict[str, dict[str, Any]] = {}
    for w in wins:
        proc = (w.get("process") or "").lower()
        if not proc:
            continue
        if proc not in seen:
            seen[proc] = {
                "process": w["process"],
                "icon": w.get("icon"),
                "sample_title": w["title"],
                "windows": 1,
                "hidden": w.get("hidden", False),
            }
        else:
            seen[proc]["windows"] += 1
            seen[proc]["hidden"] = seen[proc]["hidden"] or w.get("hidden", False)
    return sorted(seen.values(), key=lambda d: d["process"].lower())
