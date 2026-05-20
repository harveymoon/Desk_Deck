"""Chrome DevTools Protocol client — list and activate browser tabs.

Requires Chrome to be launched with --remote-debugging-port=9222. The helper
script start_chrome_debug.bat takes care of that. If the port isn't open we
return empty / available=False and the UI hides the tabs section gracefully.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.request

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
TIMEOUT_S = 0.4
_AVAIL_CACHE = {"value": False, "ts": 0.0}
_AVAIL_TTL = 5.0  # don't re-probe more than once every N seconds


def available() -> bool:
    """True if Chrome is listening on the debug port right now (cached)."""
    now = time.time()
    if now - _AVAIL_CACHE["ts"] < _AVAIL_TTL:
        return _AVAIL_CACHE["value"]
    try:
        with socket.create_connection((CDP_HOST, CDP_PORT), timeout=TIMEOUT_S):
            _AVAIL_CACHE["value"] = True
    except OSError:
        _AVAIL_CACHE["value"] = False
    _AVAIL_CACHE["ts"] = now
    return _AVAIL_CACHE["value"]


def list_tabs() -> list[dict]:
    """Return only page targets (no service workers / iframes), best-effort."""
    if not available():
        return []
    try:
        req = urllib.request.Request(f"http://{CDP_HOST}:{CDP_PORT}/json/list")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"[chrome] list_tabs failed: {e}", flush=True)
        return []

    out: list[dict] = []
    for t in data:
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        # Skip Chrome's internal pages that aren't useful as tab tiles
        if url.startswith("devtools://"):
            continue
        out.append({
            "id": t.get("id"),
            "title": t.get("title") or "(untitled)",
            "url": url,
        })
    return out


def activate_tab(tab_id: str) -> bool:
    """Make the given tab the active one in its Chrome window."""
    if not available() or not tab_id:
        return False
    try:
        req = urllib.request.Request(
            f"http://{CDP_HOST}:{CDP_PORT}/json/activate/{tab_id}",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            return True
    except Exception as e:
        print(f"[chrome] activate_tab failed: {e}", flush=True)
        return False


def find_window_for_tab(title: str):
    """After activate_tab, the matching Chrome window's title becomes
    '<tab title> - Google Chrome'. Find the HWND so we can bring it forward."""
    from . import dynamic
    wins = dynamic.enum_windows_for_process_name("chrome.exe")
    if not wins:
        return None
    # Exact substring match first
    for hwnd, win_title in wins:
        if title and title in win_title:
            return hwnd
    return wins[0][0] if wins else None
