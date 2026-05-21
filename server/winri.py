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
_AVAIL_TTL_OK = 5.0      # successes cached longer
_AVAIL_TTL_FAIL = 1.0    # failures re-probed quickly so a recent restart isn't stuck


def available() -> bool:
    """Cheap TCP probe. Cached: 5s on success, 1s on failure so a stale
    False (e.g. probed during winri startup) self-heals fast."""
    now = time.time()
    ttl = _AVAIL_TTL_OK if _AVAIL_CACHE["value"] else _AVAIL_TTL_FAIL
    if now - _AVAIL_CACHE["ts"] < ttl:
        return _AVAIL_CACHE["value"]
    try:
        with socket.create_connection((HOST, PORT), timeout=1.0):
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


def resize_to_fraction(fraction: float, window_id: int | None = None,
                       max_steps: int = 80) -> None:
    """Animate the focused window's width to ~fraction * screen_width by
    chaining width-(in|de)crement calls.

    Each native Winri width-step is a discrete jump (50 px in the user's
    config, 20 px default), so the visual "slide" effect comes from issuing
    many of them back-to-back. We:

      1. Read the live width and screen.
      2. Decide direction (grow → width-increment / shrink → width-decrement).
      3. Probe the actual step size with a single call (so we work
         regardless of the user's [tiling] resize_increment).
      4. Chain the rest of the calls to land within one step of target.

    Bounded by max_steps so a misconfig can't lock the loop. Going larger
    than ~80 steps gets noticeably slow because each HTTP roundtrip is
    ~5–30 ms — for very big jumps callers should anchor via a native
    action first (resize-halfscreen) and then call this.
    """
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
    target_id = window_id or s.get("focused_id")

    def find_win(st):
        for w in (st or {}).get("windows") or []:
            if w.get("id") == target_id:
                return w
        return None

    win = find_win(s)
    if not win:
        return
    cur = float(win.get("width") or 0)
    target = screen * float(fraction)
    diff = target - cur
    if abs(diff) < 25:
        return  # already close enough

    act = "width-increment" if diff > 0 else "width-decrement"

    # Probe step size with a single call.
    action(act)
    time.sleep(0.05)
    s = state() or s
    win2 = find_win(s)
    if not win2:
        return
    new_w = float(win2.get("width") or 0)
    step = abs(new_w - cur)
    if step < 1:
        return  # something else interfered — bail rather than spin

    remaining = max(0, int(round(abs(target - new_w) / step)))
    for _ in range(min(remaining, max_steps - 1)):
        action(act)


def resize_quarter(window_id: int | None = None) -> None:
    resize_to_fraction(0.25, window_id)


def resize_half(window_id: int | None = None) -> None:
    resize_to_fraction(0.5, window_id)


def resize_full(window_id: int | None = None) -> None:
    # Don't go to literally 1.0 of screen width — Winri's tiling padding
    # would clip it. Aim for ~98% so the chain converges quickly.
    resize_to_fraction(0.98, window_id)
