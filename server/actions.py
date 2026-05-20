"""Built-in action dispatchers."""
from __future__ import annotations

import subprocess
from typing import Any

import keyboard

from . import desktops, registry


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


_HANDLERS = {
    "hotkey": _hotkey,
    "command": _command,
    "launch": _launch,
    "focus_window": _focus_window,
    "switch_desktop": _switch_desktop,
    "python": _python,
    "chrome_tab": _chrome_tab,
}
