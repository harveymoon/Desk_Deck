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


def _rollover_par() -> tuple[dict, dict] | None:
    """Return (op_brief, par_snapshot) for the par currently under the
    mouse in TD, or None if nothing is hovered or what's hovered isn't a
    single par (could be a pargroup / page / op / panel)."""
    ro = td.state("rollover") or {}
    if ro.get("kind_of") != "par":
        return None
    op_ = ro.get("op") or {}
    par = ro.get("par") or {}
    if not op_.get("path") or not par.get("name"):
        return None
    return (op_, par)


def _td_set_par(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Push a parameter value into TouchDesigner.

    action = { type: "td_set_par", path: "/proj/noise1", par: "amp" }
    payload may carry { "value": ... } from a slider; otherwise action.value is used.

    Sentinel: path / par == "$rollover" → resolve at dispatch time from
    td.state("rollover") (kind_of=="par") so the widget always drives
    whatever's under the mouse RIGHT NOW. The slider runs in real par
    units (the server retunes its min/max/step on every rollover change),
    so the incoming value is the literal value to push — Int still gets
    rounded, Toggle gets a 0.5 threshold for safety in case something
    bool-ish drives in.
    """
    # payload-level path/par win over action-level so a smart widget
    # (e.g. the param panel) can target any par on the fly without the
    # YAML having to template per-row actions.
    path = (payload.get("path") if payload else None) or action.get("path") or ""
    par  = (payload.get("par")  if payload else None) or action.get("par")  or ""
    style = (payload.get("style") if payload else None) or None
    if path == "$rollover" or par == "$rollover":
        ro = _rollover_par()
        if ro is None:
            print("[actions] td_set_par: $rollover unresolved (nothing under mouse, or not a Par)", flush=True)
            return
        op_, p = ro
        if path == "$rollover":
            path = op_["path"]
        if par == "$rollover":
            par = p["name"]
        style = p.get("style")
    value = payload.get("value") if payload and "value" in payload else action.get("value")
    if value is None:
        return
    if style == "Int":
        try: value = int(round(float(value)))
        except (TypeError, ValueError): pass
    elif style == "Toggle":
        try: value = bool(float(value) >= 0.5)
        except (TypeError, ValueError): pass
    td.send_cmd("set_par", path=path, par=par, value=value)


def _td_macro(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    name = action.get("name")
    if not name:
        return
    td.send_cmd("macro", name=name, args=action.get("args") or {})


def _td_nudge_par(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Increment a parameter by a signed delta (value-ladder UX).

    payload.value carries the delta. With path/par == "$rollover" (or
    unset), targets the parameter currently under the mouse in TD.
    Reads current value from td.state('rollover') (kind_of=='par'), adds
    delta, sends set_par. Honours Int by rounding."""
    path = action.get("path") or ""
    par = action.get("par") or ""
    delta = None
    if payload and "value" in payload:
        delta = payload["value"]
    elif "value" in action:
        delta = action["value"]
    try:
        delta = float(delta)
    except (TypeError, ValueError):
        return
    if delta == 0:
        return

    cur_val = None
    par_style = None
    if (not path) or (not par) or path == "$rollover" or par == "$rollover":
        ro = _rollover_par()
        if ro is None:
            return
        op_, p_meta = ro
        if not path or path == "$rollover":
            path = op_["path"]
        if not par or par == "$rollover":
            par = p_meta["name"]
        cur_val = p_meta.get("value")
        par_style = p_meta.get("style")

    if cur_val is None:
        return
    try:
        new_val = float(cur_val) + delta
    except (TypeError, ValueError):
        return
    if par_style == "Int":
        new_val = int(round(new_val))
    td.send_cmd("set_par", path=path, par=par, value=new_val)


def _td_toggle_par(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Flip a Toggle-style parameter. With no `path`/`par`, targets the
    parameter currently under the mouse in TD (kind_of=='par')."""
    path = action.get("path") or ""
    par = action.get("par") or ""
    cur_val = None
    if not path or not par or path == "$rollover" or par == "$rollover":
        ro = _rollover_par()
        if ro is None:
            print("[actions] td_toggle_par: nothing single-par under mouse to toggle", flush=True)
            return
        op_, p = ro
        path = op_["path"] if not path or path == "$rollover" else path
        par = p["name"] if not par or par == "$rollover" else par
        cur_val = p.get("value")
    new_val = not bool(cur_val)
    td.send_cmd("set_par", path=path, par=par, value=new_val)


def _td_open_help(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Open the docs.derivative.ca page for the op currently under the
    mouse — falls back to the selected op when nothing is hovered."""
    import webbrowser
    # Prefer the rollover op (par/pargroup/page/op/panel all carry an op
    # brief). Falls back to whatever is selected in the network pane.
    o = None
    ro = td.state("rollover") or {}
    if ro.get("kind_of") in ("par", "pargroup", "page", "op", "panel"):
        o = ro.get("op")
    if not o:
        sel = td.state("selected") or {}
        ops = sel.get("ops") or []
        if ops:
            o = ops[0]
    if not o:
        print("[actions] td_open_help: nothing hovered or selected", flush=True)
        return
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
    "td_nudge_par": _td_nudge_par,
    "td_macro": _td_macro,
    "td_toggle_par": _td_toggle_par,
    "td_open_help": _td_open_help,
}
