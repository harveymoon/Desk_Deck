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


def thumbnail(window_id: int, width: int | None = None) -> tuple[int, bytes, str]:
    """Fetch a window thumbnail. If `width` is given, asks winri to
    downsample to that width via its `?w=NNN` query param (aspect ratio
    preserved). Older winri binaries ignore the param and return the
    native-resolution PNG — see INTEGRATION.md."""
    path = f"/windows/{window_id}/thumbnail"
    if width:
        path += f"?w={int(width)}"
    return _request("GET", path)


# Server-side thumbnail cache so concurrent tablet requests and the
# periodic strip refresh don't repeatedly poke winri.
_THUMB_CACHE: dict[tuple[int, int], tuple[float, bytes, str]] = {}
_THUMB_TTL_S = 6.0
_THUMB_CACHE_MAX = 200


def thumbnail_resized(window_id: int, max_dim: int = 320) -> tuple[int, bytes, str]:
    """Cached fetch of a downsized thumbnail.

    Delegates sizing to winri's native ?w= parameter (no PIL roundtrip on
    our end). With an updated winri binary the response is already small
    PNG; with an older one it falls back to the full-res capture.
    """
    import time

    now = time.time()
    key = (int(window_id), int(max_dim))
    cached = _THUMB_CACHE.get(key)
    if cached and now - cached[0] < _THUMB_TTL_S:
        return 200, cached[1], cached[2]

    status, body, ctype = thumbnail(window_id, width=max_dim)
    if status != 200:
        return status, body, ctype

    _THUMB_CACHE[key] = (now, body, ctype or "image/png")
    if len(_THUMB_CACHE) > _THUMB_CACHE_MAX:
        for k, _ in sorted(_THUMB_CACHE.items(), key=lambda kv: kv[1][0])[: _THUMB_CACHE_MAX // 4]:
            _THUMB_CACHE.pop(k, None)
    return status, body, ctype or "image/png"


def resize_to_fraction(fraction: float, window_id: int | None = None,
                       max_steps: int = 80) -> None:
    """Animate the focused window's width to ~fraction * screen_width by
    chaining width-(in|de)crement calls.

    Each native Winri width-step is a discrete jump (50 px in the user's
    config, 20 px default); the visual "slide" effect is many of them
    issued back-to-back. We:

      1. Read live width and screen dims.
      2. Decide direction (grow → width-increment / shrink → width-decrement).
      3. Probe step size with one call (works regardless of user's
         [tiling] resize_increment setting).
      4. Chain the rest of the calls on a single persistent HTTP/1.1
         connection (~halves end-to-end time vs. urllib.urlopen which
         re-handshakes per call).

    Bounded by max_steps so a misconfig can't lock the loop.
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

    # Probe step size with a single call (still through urllib for simplicity).
    action(act)
    time.sleep(0.04)
    s = state() or s
    win2 = find_win(s)
    if not win2:
        return
    new_w = float(win2.get("width") or 0)
    step = abs(new_w - cur)
    if step < 1:
        return  # something else interfered — bail rather than spin

    remaining = max(0, int(round(abs(target - new_w) / step)))
    n = min(remaining, max_steps - 1)
    if n <= 0:
        return
    _fast_chain(f"/action/{act}", n)


def _fast_chain(path: str, n: int) -> None:
    """POST the same path N times over a single persistent HTTP connection.

    Avoids per-call TCP setup. On localhost the difference is small
    (~1-3ms per call), but compounded over 30+ calls it roughly halves
    the total wall time, which is what makes the resize "slide" feel
    snappy instead of laggy. Suppresses individual errors so one
    transient hiccup doesn't abort the whole chain."""
    import http.client
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=TIMEOUT_S)
    except Exception as e:
        print(f"[winri] fast_chain open failed: {e}", flush=True)
        return
    try:
        for _ in range(n):
            try:
                conn.request("POST", path, headers={"Connection": "keep-alive"})
                resp = conn.getresponse()
                resp.read()
            except Exception as e:
                # Recover by reopening the connection once
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    conn = http.client.HTTPConnection(HOST, PORT, timeout=TIMEOUT_S)
                    conn.request("POST", path)
                    conn.getresponse().read()
                except Exception as e2:
                    print(f"[winri] fast_chain recovery failed: {e2}", flush=True)
                    return
    finally:
        try:
            conn.close()
        except Exception:
            pass


def resize_quarter(window_id: int | None = None) -> None:
    """Animate to ~quarter width, then center. No native quarter action."""
    resize_to_fraction(0.25, window_id)
    _settle()


def resize_half(window_id: int | None = None) -> None:
    """Animate toward half, snap to Winri's exact resize-halfscreen, then center."""
    resize_to_fraction(0.5, window_id)
    action("resize-halfscreen")
    _settle()


def resize_full(window_id: int | None = None) -> None:
    """Animate toward full, snap to Winri's exact resize-fullscreen, then center.

    The native resize-fullscreen knows how much room Winri's tiling padding
    consumes — we slide most of the way via chained width-increments for the
    visual effect, then hand off to the native action so the final width is
    truly maximal (no ~50 px gap to the screen edge)."""
    resize_to_fraction(1.0, window_id)
    action("resize-fullscreen")
    _settle()


def _settle() -> None:
    """Tiny pause then a center-focused so the window ends up framed nicely
    in the viewport after a resize."""
    time.sleep(0.05)
    try:
        action("center-focused")
    except Exception as e:
        # Older winri without center-focused → no-op
        print(f"[winri] center-focused unavailable: {e}", flush=True)
