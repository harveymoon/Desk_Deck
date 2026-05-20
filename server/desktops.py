"""Virtual desktop helpers.

Prefers pyvda (real Windows 10/11 Virtual Desktop API) and falls back to
Win+Ctrl+Left/Right keyboard cycling if pyvda isn't available.
"""
from __future__ import annotations

from typing import Any

try:
    import pyvda  # type: ignore
    _AVAILABLE = True
except Exception as e:  # pragma: no cover
    pyvda = None  # type: ignore
    _AVAILABLE = False
    _IMPORT_ERROR = str(e)


def available() -> bool:
    return _AVAILABLE


def list_desktops() -> list[dict[str, Any]]:
    """Return [{index, name, current}], 1-indexed per pyvda."""
    if not _AVAILABLE:
        return []
    try:
        desks = pyvda.get_virtual_desktops()
        current_num = pyvda.VirtualDesktop.current().number
        out = []
        for d in desks:
            try:
                name = d.name or f"Desktop {d.number}"
            except Exception:
                name = f"Desktop {d.number}"
            out.append({
                "index": d.number,
                "name": name,
                "current": d.number == current_num,
            })
        return out
    except Exception as e:
        print(f"[desktops] list failed: {e}", flush=True)
        return []


def switch_to(index: int) -> bool:
    """Switch to the 1-indexed desktop. Returns True on success."""
    if not _AVAILABLE:
        return _switch_via_keys(index)
    try:
        pyvda.VirtualDesktop(int(index)).go()
        return True
    except Exception as e:
        print(f"[desktops] switch_to({index}) failed: {e}", flush=True)
        return _switch_via_keys(index)


def _switch_via_keys(index: int) -> bool:
    """Crude fallback: Win+Ctrl+Right N times. Only works for forward jumps."""
    try:
        import keyboard
        # Best-effort: cycle right `index` times. Not perfect — only works
        # when we don't know our current position. Used only if pyvda is missing.
        for _ in range(max(0, int(index) - 1)):
            keyboard.send("win+ctrl+right")
        return True
    except Exception:
        return False
