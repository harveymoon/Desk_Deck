"""Tiny HTTP client for the local winri tiling-WM control API.

Winri lives at http://127.0.0.1:47812 by default (loopback only, no auth)
and only listens once the user has set `[api] enabled = true` in
`%APPDATA%\\winri\\config.toml`. We proxy everything through the Desk_Deck
server so the tablet (on a LAN IP) can reach it via our token-protected
routes.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

HOST = "127.0.0.1"
PORT = 47812
TIMEOUT_S = 1.0
_BASE = f"http://{HOST}:{PORT}"

_AVAIL_CACHE = {"value": False, "ts": 0.0}
_AVAIL_TTL = 4.0


def available() -> bool:
    """Cheap TCP probe — cached for a few seconds so the poll loop isn't
    hammering the port when winri's API is disabled."""
    now = time.time()
    if now - _AVAIL_CACHE["ts"] < _AVAIL_TTL:
        return _AVAIL_CACHE["value"]
    try:
        with socket.create_connection((HOST, PORT), timeout=0.3):
            _AVAIL_CACHE["value"] = True
    except OSError:
        _AVAIL_CACHE["value"] = False
    _AVAIL_CACHE["ts"] = now
    return _AVAIL_CACHE["value"]


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, bytes, str]:
    """Returns (status_code, body_bytes, content_type). Raises on transport errors."""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "application/json") if e.headers else "application/json"


def state() -> dict[str, Any] | None:
    if not available():
        return None
    try:
        status, body, _ = _request("GET", "/state")
        if status != 200:
            return None
        return json.loads(body)
    except Exception as e:
        print(f"[winri] state failed: {e}", flush=True)
        return None


def windows() -> list[dict[str, Any]]:
    if not available():
        return []
    try:
        status, body, _ = _request("GET", "/windows")
        if status != 200:
            return []
        return json.loads(body)
    except Exception as e:
        print(f"[winri] windows failed: {e}", flush=True)
        return []


def action(name: str) -> tuple[int, bytes, str]:
    return _request("POST", f"/action/{name}")


def focus(window_id: int) -> tuple[int, bytes, str]:
    return _request("POST", f"/focus/{window_id}")


def scroll(*, delta: float | None = None, offset: float | None = None) -> tuple[int, bytes, str]:
    body: dict[str, Any] = {}
    if offset is not None:
        body["offset"] = offset
    elif delta is not None:
        body["delta"] = delta
    return _request("POST", "/scroll", body)


def thumbnail(window_id: int) -> tuple[int, bytes, str]:
    return _request("GET", f"/windows/{window_id}/thumbnail")


def resize_quarter(window_id: int | None = None) -> None:
    """Winri has no direct quarter-screen action — approximate by triggering
    resize-halfscreen then halving via repeated width-decrement steps.

    Each width-decrement reduces the focused window by ~20px (winri default).
    We compute steps from the live state so it lands close to screen_width/4."""
    s = state()
    if not s:
        return
    if window_id and s.get("focused_id") != window_id:
        focus(window_id)
        time.sleep(0.05)
        s = state() or s
    screen = float(s.get("screen_width") or 0)
    if screen <= 0:
        return
    # Start from half then trim
    action("resize-halfscreen")
    time.sleep(0.05)
    s = state() or s
    win = None
    target_id = window_id or s.get("focused_id")
    for w in s.get("windows") or []:
        if w.get("id") == target_id:
            win = w
            break
    if not win:
        return
    current = float(win.get("width") or 0)
    target = screen / 4.0
    steps = max(0, int((current - target) / 20.0))
    for _ in range(min(steps, 40)):  # safety bound
        action("width-decrement")
