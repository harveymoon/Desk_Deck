"""Built-in action dispatchers."""
from __future__ import annotations

import subprocess
from typing import Any

import keyboard

from . import desktops, registry, td


def dispatch(action: dict[str, Any] | None, payload: dict[str, Any] | None = None,
             context: dict[str, Any] | None = None, widget: dict[str, Any] | None = None) -> None:
    if not action:
        return
    kind = action.get("type")
    handler = _HANDLERS.get(kind)
    if not handler:
        print(f"[actions] unknown action type: {kind}", flush=True)
        return
    try:
        handler(action, payload or {}, context or {}, widget or {})
    except Exception as e:
        print(f"[actions] {kind} failed: {e}", flush=True)


def _hotkey(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    keys = action.get("keys")
    if not keys:
        return
    keyboard.send(keys)


def _command(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    cmd = action.get("cmd")
    if not cmd:
        return
    subprocess.Popen(cmd, shell=isinstance(cmd, str))


def _launch(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Open a file, URL, or app via the OS shell."""
    target = action.get("target")
    if not target:
        return
    try:
        import os
        os.startfile(target)
    except Exception as e:
        print(f"[actions] launch failed: {e}", flush=True)


def _focus_window(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Bring a window to the foreground using AttachThreadInput."""
    hwnd = payload.get("hwnd") or action.get("hwnd")
    if not hwnd:
        return
    try:
        import ctypes
        import win32con
        import win32gui
        import win32process

        hwnd = int(hwnd)
        if not win32gui.IsWindow(hwnd):
            print(f"[actions] focus_window: hwnd {hwnd} no longer exists", flush=True)
            return

        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return

        my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
        attached = False
        if fg_tid and fg_tid != my_tid:
            attached = bool(ctypes.windll.user32.AttachThreadInput(my_tid, fg_tid, True))
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                ctypes.windll.user32.AttachThreadInput(my_tid, fg_tid, False)
    except Exception as e:
        print(f"[actions] focus_window: {e}", flush=True)


def _switch_desktop(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    index = payload.get("index") or action.get("index")
    if index is None:
        return
    desktops.switch_to(int(index))


def _python(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    provider = action.get("provider")
    if not provider:
        return
    # If payload carries a value (slider), use it; else default to "press"
    value = payload.get("value", "press")
    registry.call_on_value(provider, value, widget, context)


def _chrome_tab(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Activate a Chrome tab via CDP and bring its Chrome window to the front."""
    from . import chrome as cdp
    tab_id = action.get("tab_id") or payload.get("tab_id")
    if not tab_id:
        return
    if not cdp.activate_tab(tab_id):
        return
    # Match the now-active tab's title to the OS-level Chrome window and focus it.
    tab = next((t for t in cdp.list_tabs() if t.get("id") == tab_id), None)
    title = (tab or {}).get("title") or ""
    hwnd = cdp.find_window_for_tab(title)
    if hwnd:
        _focus_window({"hwnd": hwnd}, {}, context, widget)


def _td_set_par(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Push a parameter value into TouchDesigner.

    action = { type: "td_set_par", path: "/proj/noise1", par: "amp" }
    payload may carry { "value": ... } from a slider; otherwise action.value is used.

    Sentinel: path / par == "$rollover" → resolve at dispatch time from
    td.state("rollover_par") so the widget always drives whatever's under
    the mouse RIGHT NOW. When both path and par are $rollover (the
    quick-adjust slider case), we also map the tablet's 0..1 slider
    range onto the par's normMin..normMax so a fixed slider widget
    drives any parameter sensibly.
    """
    path = action.get("path") or ""
    par = action.get("par") or ""
    is_rollover = (path == "$rollover" and par == "$rollover")
    if path == "$rollover" or par == "$rollover":
        rp = td.state("rollover_par") or {}
        op_ = (rp.get("op") or {})
        p = (rp.get("par") or {})
        if not op_.get("path") or not p.get("name"):
            print("[actions] td_set_par: $rollover unresolved (no par under mouse)", flush=True)
            return
        if path == "$rollover":
            path = op_["path"]
        if par == "$rollover":
            par = p["name"]
        if is_rollover:
            # Map 0..1 from the tablet slider → par's normMin..normMax
            nmin = float(p.get("normMin") or 0.0)
            nmax = float(p.get("normMax") or 1.0)
            raw = payload.get("value") if payload and "value" in payload else action.get("value")
            if raw is None:
                return
            try:
                raw = float(raw)
            except (TypeError, ValueError):
                return
            value = nmin + max(0.0, min(1.0, raw)) * (nmax - nmin)
            # Honor par type — Toggle wants a bool, Int rounds.
            style = p.get("style")
            if style == "Toggle":
                value = bool(value >= 0.5)
            elif style == "Int":
                value = int(round(value))
            td.send_cmd("set_par", path=path, par=par, value=value)
            return
    value = payload.get("value") if payload and "value" in payload else action.get("value")
    if value is None:
        return
    td.send_cmd("set_par", path=path, par=par, value=value)


def _td_macro(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    name = action.get("name")
    if not name:
        return
    td.send_cmd("macro", name=name, args=action.get("args") or {})


def _td_open_help(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Open the docs.derivative.ca page for the currently-selected op."""
    import webbrowser
    sel = td.state("selected") or {}
    ops = sel.get("ops") or []
    if not ops:
        print("[actions] td_open_help: no selection", flush=True)
        return
    o = ops[0]
    op_type = o.get("type") or ""
    family = o.get("family") or ""
    if not op_type or not family or not op_type.endswith(family):
        print(f"[actions] td_open_help: bad op type {op_type!r} family {family!r}", flush=True)
        return
    head = op_type[: -len(family)]
    slug = f"{head[:1].upper()}{head[1:]}_{family.upper()}"
    url = f"https://docs.derivative.ca/{slug}"
    if action.get("python"):
        url += "_Class"
    print(f"[actions] td_open_help → {url}", flush=True)
    webbrowser.open(url)


_HANDLERS = {
    "hotkey": _hotkey,
    "command": _command,
    "launch": _launch,
    "focus_window": _focus_window,
    "switch_desktop": _switch_desktop,
    "python": _python,
    "chrome_tab": _chrome_tab,
    "td_set_par": _td_set_par,
    "td_macro": _td_macro,
    "td_open_help": _td_open_help,
}
