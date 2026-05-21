"""FastAPI app: HTTP routes + WebSocket hub.

Responsibilities:
- Serve the tablet runtime and the visual editor.
- Token-gate all data routes.
- Detect foreground app, push a matching YAML layout (or a synthetic
  window-list fallback) over WS. Hot-reload on YAML edit.
- Push themes alongside layouts.
- Expose windows / virtual-desktops / bookmarks JSON for overlays.
- Accept external pushes to textboxes via POST /widget/:id.
- Subscribe textbox widgets to provider streams.
"""
from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions, auth, chrome, config, desktops, dynamic, filters, log_buffer, registry, sidebar, td, themes, watcher, winri

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Desk_Deck")

app.mount("/shared", StaticFiles(directory=WEB_DIR / "shared"), name="shared")
app.mount("/runtime-static", StaticFiles(directory=WEB_DIR / "runtime"), name="runtime")
app.mount("/editor-static", StaticFiles(directory=WEB_DIR / "editor"), name="editor-static")


# ───────── state ─────────

_loop: asyncio.AbstractEventLoop | None = None
_current_context: dict = {"process": "", "title": "", "win_class": "", "pid": 0}
_last_window_fingerprint: tuple = ()
_last_window_list_fp: tuple = ()
_window_poll_task: asyncio.Task | None = None
_desktop_poll_task: asyncio.Task | None = None
_last_desktops_fp: tuple = ()


class Hub:
    """Tracks WS clients, what each was last sent (dedupe), and active subscriptions."""

    def __init__(self) -> None:
        self.clients: dict[WebSocket, dict] = {}
        self.lock = asyncio.Lock()

    async def add(self, ws: WebSocket, device: str = "tablet") -> None:
        async with self.lock:
            self.clients[ws] = {
                "device": device,
                "last_sent": "",
                "cfg": None,
                "textbox_subs": {},  # widget_id -> cleanup callable
            }

    async def remove(self, ws: WebSocket) -> None:
        async with self.lock:
            state = self.clients.pop(ws, None)
            if state:
                for cleanup in state.get("textbox_subs", {}).values():
                    try:
                        cleanup()
                    except Exception:
                        pass

    async def broadcast_layout(self, cfg: dict | None, theme: dict | None) -> None:
        if cfg is None:
            return
        payload = json.dumps({"t": "layout", "layout": cfg, "theme": theme})
        async with self.lock:
            dead = []
            for ws, state in self.clients.items():
                if state["device"] != "tablet":
                    continue
                if state["last_sent"] == payload:
                    continue
                try:
                    await ws.send_text(payload)
                    state["last_sent"] = payload
                    state["cfg"] = cfg
                    self._refresh_textbox_subs(ws, state, cfg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.pop(ws, None)

    async def send_to(self, ws: WebSocket, msg: dict) -> None:
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            pass

    async def send_to_all(self, msg: dict) -> None:
        data = json.dumps(msg)
        async with self.lock:
            dead = []
            for ws in self.clients:
                try:
                    await ws.send_text(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.pop(ws, None)

    async def push_widget_update(self, widget_id: str, patch: dict) -> None:
        """Send a widget_update to every client whose current cfg has the widget."""
        msg = json.dumps({"t": "widget_update", "id": widget_id, "patch": patch})
        async with self.lock:
            dead = []
            for ws, state in self.clients.items():
                cfg = state.get("cfg") or {}
                if any(w.get("id") == widget_id for w in cfg.get("widgets") or []):
                    try:
                        await ws.send_text(msg)
                    except Exception:
                        dead.append(ws)
            for ws in dead:
                self.clients.pop(ws, None)

    def _refresh_textbox_subs(self, ws: WebSocket, state: dict, cfg: dict) -> None:
        """Set up provider subscriptions for any textbox widgets in cfg."""
        # Tear down old subs first
        for cleanup in state.get("textbox_subs", {}).values():
            try:
                cleanup()
            except Exception:
                pass
        state["textbox_subs"] = {}

        for w in cfg.get("widgets") or []:
            if w.get("type") != "textbox":
                continue
            wid = w.get("id")
            source = (w.get("props") or {}).get("source")
            if not wid or not source:
                continue

            # emit() is called from the provider's thread; bridge to asyncio.
            # Providers may call emit(text) to append, or emit(text, replace=True)
            # to replace the whole textbox content.
            def make_emit(captured_wid: str):
                def emit(text: str, replace: bool = False) -> None:
                    if _loop is not None:
                        patch = {"replace": text} if replace else {"append": text}
                        asyncio.run_coroutine_threadsafe(
                            self.push_widget_update(captured_wid, patch),
                            _loop,
                        )
                return emit

            cleanup = registry.subscribe_to(source, make_emit(wid))
            if cleanup:
                state["textbox_subs"][wid] = cleanup

    def cfg_for(self, ws: WebSocket) -> dict | None:
        return (self.clients.get(ws) or {}).get("cfg")


hub = Hub()


# ───────── lifecycle ─────────

@app.on_event("startup")
async def _on_startup() -> None:
    global _loop, _window_poll_task, _desktop_poll_task
    _loop = asyncio.get_running_loop()

    registry.reload()
    watcher.start(on_change=_on_context_change)
    config.watch(on_change=_on_config_change)
    themes.watch(on_change=_on_theme_change)
    _window_poll_task = asyncio.create_task(_poll_window_changes())
    _desktop_poll_task = asyncio.create_task(_poll_desktop_changes())

    # When TD reports a new parameter under the mouse, also nudge any
    # tablet widget with id="td_rollover_drive" so its slider position
    # reflects the new par's current value (mapped to 0..1).
    td.subscribe("rollover_par", _on_td_rollover_par)

    print("[startup] watcher + hot-reload + window/desktop pollers running", flush=True)


def _on_td_rollover_par(payload: dict | None) -> None:
    if _loop is None:
        return
    p = ((payload or {}).get("par") or {})
    style = p.get("style")
    val = p.get("value")

    # Slider gets normalized value (0..1) AND a disabled flag when the par
    # under the mouse is a Toggle (sliders don't make sense for booleans).
    is_toggle = (style == "Toggle")
    if val is not None:
        try:
            v = float(val) if not isinstance(val, bool) else (1.0 if val else 0.0)
        except (TypeError, ValueError):
            v = None
        if v is not None:
            nmin = float(p.get("normMin") or 0.0)
            nmax = float(p.get("normMax") or 1.0)
            span = nmax - nmin
            norm = 0.0 if span == 0 else max(0.0, min(1.0, (v - nmin) / span))
            asyncio.run_coroutine_threadsafe(
                hub.push_widget_update("td_rollover_drive",
                                       {"value": norm, "disabled": is_toggle}),
                _loop,
            )

    # Dedicated toggle button. Reflects on/off state and label; only
    # enabled when the par under the mouse is actually a Toggle.
    is_on = False
    if is_toggle:
        is_on = bool(val) if val is not None else False
    label = f"{p.get('name','?').upper()}: {'ON' if is_on else 'OFF'}" if is_toggle else "(not a toggle)"
    asyncio.run_coroutine_threadsafe(
        hub.push_widget_update("td_rollover_toggle",
                               {"active": is_on, "disabled": not is_toggle, "label": label}),
        _loop,
    )


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    global _window_poll_task, _desktop_poll_task
    for t in (_window_poll_task, _desktop_poll_task):
        if t is not None:
            t.cancel()
    _window_poll_task = None
    _desktop_poll_task = None
    watcher.stop()
    config.stop_watching()
    themes.stop_watching()


# ───────── change hooks ─────────

def _on_context_change(process: str, title: str, win_class: str, pid: int) -> None:
    prev_proc = _current_context.get("process")
    prev_class = _current_context.get("win_class")
    _current_context.update(process=process, title=title, win_class=win_class, pid=pid)
    if process != prev_proc or win_class != prev_class:
        print(f"[ctx] process={process!r} class={win_class!r} pid={pid}", flush=True)
    _schedule_broadcast()


def _on_config_change() -> None:
    print("[config] reload", flush=True)
    _schedule_broadcast()


def _on_theme_change() -> None:
    print("[theme] reload", flush=True)
    _schedule_broadcast()


def _schedule_broadcast() -> None:
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast_active(), _loop)


async def _broadcast_active() -> None:
    global _last_window_fingerprint, _last_window_list_fp, _last_raw_cfg
    raw = _resolve_raw()
    _last_raw_cfg = raw
    cfg = _expand_dynamic_widgets(raw) if raw else None
    cfg = _augment_with_context(cfg) if cfg else None
    theme = _theme_for(cfg)
    if cfg and cfg.get("synthetic"):
        _last_window_fingerprint = dynamic.fingerprint(
            _current_context.get("pid", 0),
            _current_context.get("process", ""),
        )
    _last_window_list_fp = dynamic.window_list_fingerprint(raw, _current_context)
    await hub.broadcast_layout(cfg, theme)


def _resolve_raw() -> dict | None:
    """Same as _resolve_layout but returns the cfg BEFORE expanding window_list
    widgets (used for fingerprinting and re-broadcasting)."""
    process = _current_context.get("process", "")
    title = _current_context.get("title", "")
    win_class = _current_context.get("win_class", "")
    pid = _current_context.get("pid", 0)

    cfg = config.match(process, title, win_class)
    if cfg and cfg.get("name", "").lower() != "default":
        return cfg
    if process:
        synthetic = dynamic.generate_fallback_layout(process, pid)
        if synthetic:
            return synthetic
    return cfg


def _resolve_layout() -> dict | None:
    process = _current_context.get("process", "")
    title = _current_context.get("title", "")
    win_class = _current_context.get("win_class", "")
    pid = _current_context.get("pid", 0)

    cfg = config.match(process, title, win_class)
    if cfg and cfg.get("name", "").lower() != "default":
        return _augment_with_context(_expand_dynamic_widgets(cfg))

    if process:
        synthetic = dynamic.generate_fallback_layout(process, pid)
        if synthetic:
            return _augment_with_context(synthetic)

    if cfg:  # default.yaml
        return _augment_with_context(_expand_dynamic_widgets(cfg))
    return None


def _expand_dynamic_widgets(cfg: dict) -> dict:
    """Replace any window_list widgets in cfg with their expanded button grids."""
    widgets = cfg.get("widgets") or []
    if not any(w.get("type") == "window_list" for w in widgets):
        return cfg
    out: list[dict] = []
    for w in widgets:
        if w.get("type") == "window_list":
            out.extend(dynamic.expand_window_list(w, _current_context))
        else:
            out.append(w)
    return {**cfg, "widgets": out}


def _augment_with_context(cfg: dict) -> dict:
    """Attach context metadata to the layout so the runtime title bar can render it."""
    out = dict(cfg)
    out["_context"] = {
        "process": _current_context.get("process", ""),
        "title": _current_context.get("title", ""),
        "win_class": _current_context.get("win_class", ""),
        "pid": _current_context.get("pid", 0),
        "matched": bool(cfg.get("name") and cfg.get("name", "").lower() != "default" and not cfg.get("synthetic")),
        "synthetic": bool(cfg.get("synthetic")),
    }
    return out


def _theme_for(cfg: dict | None) -> dict:
    name = None
    if cfg:
        name = (cfg.get("canvas") or {}).get("theme")
    return themes.get(name)


async def _poll_window_changes() -> None:
    """Once per second, rebuild and rebroadcast when anything driving a
    dynamic widget has changed (synthetic fallback, window_list contents,
    chrome_tabs). The hub's per-client dedupe means no-op ticks are free."""
    global _last_window_fingerprint, _last_window_list_fp
    while True:
        try:
            await asyncio.sleep(1.0)
            raw = _last_raw_cfg or {}
            showing_synthetic = bool(raw.get("synthetic"))
            has_window_list = any(
                w.get("type") == "window_list"
                for w in (raw.get("widgets") or [])
            )

            cur_fp_proc = dynamic.fingerprint(
                _current_context.get("pid", 0),
                _current_context.get("process", ""),
            )
            cur_fp_wl = (
                dynamic.window_list_fingerprint(raw, _current_context)
                if has_window_list else ()
            )

            proc_changed = showing_synthetic and cur_fp_proc != _last_window_fingerprint
            wl_changed = has_window_list and cur_fp_wl != _last_window_list_fp

            if proc_changed or wl_changed:
                _last_window_fingerprint = cur_fp_proc
                _last_window_list_fp = cur_fp_wl
                await _broadcast_active()
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[poll] window-change loop error: {e}", flush=True)


# Most recently resolved RAW cfg (pre-expansion). The poller fingerprints
# any window_list widgets in here to decide whether to rebroadcast.
_last_raw_cfg: dict | None = None


async def _poll_desktop_changes() -> None:
    """Once every 2s, notify clients of virtual-desktop state changes so the
    Spaces overlay stays fresh."""
    global _last_desktops_fp
    while True:
        try:
            await asyncio.sleep(2.0)
            ds = desktops.list_desktops()
            fp = tuple((d["index"], d["name"], d["current"]) for d in ds)
            if fp != _last_desktops_fp:
                _last_desktops_fp = fp
                await hub.send_to_all({"t": "desktops", "desktops": ds})
        except asyncio.CancelledError:
            return
        except Exception:
            pass


# ───────── HTTP routes ─────────

@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "runtime" / "index.html")


@app.get("/editor", response_class=HTMLResponse)
def editor_page() -> Response:
    p = WEB_DIR / "editor" / "index.html"
    if p.exists():
        return FileResponse(p)
    return HTMLResponse("<h1>Editor not built</h1>", status_code=200)


@app.get("/pair", response_class=HTMLResponse)
def pair_page(request: Request) -> HTMLResponse:
    host_ip = _lan_ip()
    url = f"http://{host_ip}:{request.url.port or 8765}/?t={auth.get_token()}"
    import qrcode, io, base64
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset=utf-8><title>Pair Desk_Deck</title>
<link rel=icon href=/runtime-static/icon.svg type=image/svg+xml>
<style>
  body {{ background:#0a0a0c; color:#e6e6ea; font-family:system-ui,sans-serif;
          display:flex; align-items:center; justify-content:center;
          min-height:100vh; margin:0; flex-direction:column; gap:1.5rem; }}
  .qr {{ background:#fff; padding:16px; border:1px solid #2a2a32; }}
  code {{ background:#1c1c22; padding:6px 10px; border:1px solid #2a2a32;
          font-family:ui-monospace,monospace; user-select:all; }}
  h1 {{ margin:0; font-weight:400; letter-spacing:.08em; font-size:1rem;
        color:#7a7a85; text-transform:uppercase; }}
</style></head>
<body>
  <h1>Scan on tablet</h1>
  <div class=qr><img src="data:image/png;base64,{b64}" width=320 height=320></div>
  <code>{url}</code>
</body></html>""")


@app.get("/api/token")
def api_token(request: Request) -> JSONResponse:
    if not auth.is_localhost(request):
        raise HTTPException(403, "localhost only")
    return JSONResponse({"token": auth.get_token()})


@app.get("/api/configs")
def list_configs(request: Request) -> JSONResponse:
    auth.require_token(request)
    return JSONResponse([{"name": c["name"]} for c in config.load_all()])


@app.get("/api/configs/{name}")
def get_config(name: str, request: Request) -> JSONResponse:
    auth.require_token(request)
    cfg = config.load_one(name)
    if not cfg:
        raise HTTPException(404, "not found")
    return JSONResponse(cfg)


class ConfigBody(BaseModel):
    name: str | None = None
    match: dict | None = None
    canvas: dict | None = None
    widgets: list | None = None


@app.put("/api/configs/{name}")
def save_config(name: str, request: Request, body: dict) -> JSONResponse:
    auth.require_token(request)
    safe = _safe_filename(name)
    if not safe:
        raise HTTPException(400, "invalid name")
    body.setdefault("name", name)
    config.save(safe, body)
    return JSONResponse({"ok": True, "name": safe})


@app.delete("/api/configs/{name}")
def delete_config(name: str, request: Request) -> JSONResponse:
    auth.require_token(request)
    safe = _safe_filename(name)
    if not safe:
        raise HTTPException(400, "invalid name")
    ok = config.delete(safe)
    if not ok:
        raise HTTPException(404, "not found")
    return JSONResponse({"ok": True})


@app.get("/api/themes")
def list_themes(request: Request) -> JSONResponse:
    auth.require_token(request)
    return JSONResponse([{"name": n} for n in themes.names()])


@app.get("/api/themes/{name}")
def get_theme(name: str, request: Request) -> JSONResponse:
    auth.require_token(request)
    return JSONResponse(themes.get(name))


@app.get("/api/context")
def get_context(request: Request) -> JSONResponse:
    auth.require_token(request)
    cfg = _resolve_layout() or {}
    return JSONResponse({
        **_current_context,
        "active_config": cfg.get("name"),
        "synthetic": bool(cfg.get("synthetic")),
    })


@app.get("/api/windows")
def list_windows(request: Request, include_hidden: bool = False) -> JSONResponse:
    """All visible top-level windows across all processes (for the Apps overlay).

    By default applies the filter from configs/_filters.yaml. Pass
    ?include_hidden=true to see filtered entries too (used by the editor)."""
    auth.require_token(request)
    return JSONResponse(dynamic.enum_all_visible_windows(include_hidden=include_hidden))


@app.get("/api/processes")
def list_processes(request: Request) -> JSONResponse:
    """Distinct processes with at least one visible top-level window (with icons).
    Used by the editor's Hidden Apps modal."""
    auth.require_token(request)
    return JSONResponse(dynamic.enum_distinct_processes(include_hidden=True))


@app.get("/api/logs")
def get_logs(request: Request) -> JSONResponse:
    """Snapshot of recent log lines (in-memory ring buffer)."""
    auth.require_token(request)
    return JSONResponse(log_buffer.snapshot())


@app.get("/api/logs/stream")
async def stream_logs(request: Request):
    """Server-Sent Events stream of log lines: snapshot first, then live."""
    from fastapi.responses import StreamingResponse
    auth.require_token(request)
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)

    async def gen():
        try:
            # Initial snapshot so the user sees recent history immediately.
            for entry in log_buffer.snapshot():
                yield f"data: {json.dumps(entry)}\n\n"
            log_buffer.add_listener(loop, q)
            # Live updates, with periodic keepalive so proxies don't drop us.
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            log_buffer.remove_listener(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/sidebar")
def get_sidebar(request: Request) -> JSONResponse:
    auth.require_token(request)
    return JSONResponse(sidebar.load())


@app.put("/api/sidebar")
async def put_sidebar(request: Request) -> JSONResponse:
    auth.require_token(request)
    body = await request.json()
    sidebar.save(body)
    return JSONResponse({"ok": True})


@app.get("/api/filters")
def get_filters(request: Request) -> JSONResponse:
    auth.require_token(request)
    return JSONResponse(filters.load())


@app.put("/api/filters")
async def put_filters(request: Request) -> JSONResponse:
    auth.require_token(request)
    body = await request.json()
    filters.save(body)
    return JSONResponse({"ok": True})


@app.get("/api/chrome/tabs")
async def list_chrome_tabs(request: Request) -> JSONResponse:
    """List Chrome tabs via DevTools Protocol. Empty if --remote-debugging-port=9222
    isn't enabled. Use start_chrome_debug.bat to launch Chrome with the flag.

    Tabs are enriched with window_id (the CDP-side window they belong to) and
    grouped into windows[] so the client can render a tree."""
    auth.require_token(request)
    if not chrome.available():
        return JSONResponse({"available": False, "tabs": [], "windows": []})
    tabs = await chrome.list_tabs_with_windows_async()
    by_window: dict[int | None, list] = {}
    for t in tabs:
        by_window.setdefault(t.get("window_id"), []).append(t)
    windows = [
        {"window_id": wid, "tabs": tlist}
        for wid, tlist in sorted(by_window.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
    ]
    return JSONResponse({"available": True, "tabs": tabs, "windows": windows})


@app.post("/api/chrome/activate/{tab_id}")
def activate_chrome_tab(tab_id: str, request: Request) -> JSONResponse:
    """Activate a Chrome tab AND bring its Chrome window to the OS foreground."""
    auth.require_token(request)
    # Pull title before activating — we use it to find the right Chrome window after.
    tabs = chrome.list_tabs()
    tab = next((t for t in tabs if t.get("id") == tab_id), None)
    ok = chrome.activate_tab(tab_id)
    if ok and tab:
        hwnd = chrome.find_window_for_tab(tab.get("title") or "")
        if hwnd:
            actions.dispatch({"type": "focus_window", "hwnd": hwnd})
    return JSONResponse({"ok": ok})


@app.get("/api/desktops")
def list_virtual_desktops(request: Request) -> JSONResponse:
    auth.require_token(request)
    return JSONResponse({"available": desktops.available(), "desktops": desktops.list_desktops()})


@app.post("/api/desktops/{index}")
def switch_virtual_desktop(index: int, request: Request) -> JSONResponse:
    auth.require_token(request)
    ok = desktops.switch_to(index)
    return JSONResponse({"ok": ok})


# ───────── Winri (local tiling-WM HTTP API) proxy ─────────

@app.get("/api/winri/state")
def winri_state(request: Request) -> JSONResponse:
    auth.require_token(request)
    s = winri.state()
    if s is None:
        return JSONResponse({"available": False, "error": "winri api unreachable on 127.0.0.1:47812"}, status_code=503)
    return JSONResponse({"available": True, **s})


@app.get("/api/winri/windows")
def winri_windows(request: Request) -> JSONResponse:
    auth.require_token(request)
    return JSONResponse({"available": winri.available(), "windows": winri.windows()})


@app.post("/api/winri/action/{name}")
def winri_action(name: str, request: Request) -> JSONResponse:
    auth.require_token(request)
    try:
        status, _, _ = winri.action(name)
        return JSONResponse({"ok": status < 400, "status": status})
    except Exception as e:
        raise HTTPException(503, f"winri unreachable: {e}")


@app.post("/api/winri/focus/{wid}")
def winri_focus(wid: int, request: Request) -> JSONResponse:
    auth.require_token(request)
    try:
        status, _, _ = winri.focus(wid)
        return JSONResponse({"ok": status < 400, "status": status})
    except Exception as e:
        raise HTTPException(503, f"winri unreachable: {e}")


class _WinriScrollBody(BaseModel):
    delta: float | None = None
    offset: float | None = None


@app.post("/api/winri/scroll")
async def winri_scroll(request: Request) -> JSONResponse:
    auth.require_token(request)
    body = await request.json()
    try:
        status, _, _ = winri.scroll(delta=body.get("delta"), offset=body.get("offset"))
        return JSONResponse({"ok": status < 400, "status": status})
    except Exception as e:
        raise HTTPException(503, f"winri unreachable: {e}")


@app.post("/api/winri/resize/{kind}")
async def winri_resize_smooth(kind: str, request: Request) -> JSONResponse:
    """Animated resize via chained width-(in|de)crement.

    kind ∈ {"quarter", "half", "full"}. Each chains a series of native
    width-step actions so the window appears to slide to the target size,
    rather than snapping instantly the way Winri's native resize-* actions
    do. Step size is detected live so it works regardless of the user's
    [tiling] resize_increment setting."""
    auth.require_token(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    wid = body.get("window_id") if isinstance(body, dict) else None
    target_id = int(wid) if wid else None
    fn = {
        "quarter": winri.resize_quarter,
        "half":    winri.resize_half,
        "full":    winri.resize_full,
    }.get(kind)
    if not fn:
        raise HTTPException(400, f"unknown resize kind: {kind}")
    try:
        fn(target_id)
        return JSONResponse({"ok": True, "kind": kind})
    except Exception as e:
        raise HTTPException(503, f"winri unreachable: {e}")


@app.get("/api/winri/thumbnail/{wid}")
def winri_thumbnail(wid: int, request: Request, w: int = 320) -> Response:
    """Cached pass-through of winri's window thumbnail, with `w` forwarded
    to winri's native `?w=NNN` downsampling. Server caches the result for
    ~6s so a strip refresh doesn't hammer winri."""
    auth.require_token(request)
    try:
        status, body, ctype = winri.thumbnail_resized(wid, max_dim=max(32, min(w, 1024)))
        if status != 200:
            return Response(body, status_code=status, media_type=ctype)
        return Response(body, media_type=ctype or "image/png",
                        headers={"Cache-Control": "no-store"})
    except Exception as e:
        raise HTTPException(503, f"winri unreachable: {e}")


@app.post("/api/focus/{hwnd}")
def focus_hwnd(hwnd: int, request: Request) -> JSONResponse:
    auth.require_token(request)
    actions.dispatch({"type": "focus_window", "hwnd": hwnd})
    return JSONResponse({"ok": True})


@app.get("/api/bookmarks")
def get_bookmarks(request: Request) -> JSONResponse:
    auth.require_token(request)
    b = config.load_one("bookmarks")
    return JSONResponse(b or {"name": "bookmarks", "widgets": []})


@app.post("/api/bookmarks/run")
async def run_bookmark(request: Request) -> JSONResponse:
    """Dispatch a bookmark action — used by the overlay since bookmarks aren't
    in the active layout. Body: {"action": {...}, "payload": {...}}"""
    auth.require_token(request)
    body = await request.json()
    action = body.get("action") or {}
    payload = body.get("payload") or {}
    actions.dispatch(action, payload=payload, context=_current_context)
    return JSONResponse({"ok": True})


@app.post("/api/action")
async def run_action(request: Request) -> JSONResponse:
    """Dispatch any action — useful for ad-hoc UI and the editor's quick-test."""
    auth.require_token(request)
    body = await request.json()
    actions.dispatch(body.get("action") or {}, payload=body.get("payload") or {},
                     context=_current_context)
    return JSONResponse({"ok": True})


@app.post("/widget/{wid}")
async def push_to_widget(wid: str, request: Request) -> JSONResponse:
    """External hook: append text to a textbox widget (or replace its contents).

    Body: {"text": "..."} appends; {"replace": "..."} replaces.
    """
    auth.require_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    patch: dict[str, Any] = {}
    if "text" in body:
        patch["append"] = str(body["text"])
    if "replace" in body:
        patch["replace"] = str(body["replace"])
    if not patch:
        raise HTTPException(400, "body must include 'text' or 'replace'")
    await hub.push_widget_update(wid, patch)
    return JSONResponse({"ok": True})


def _safe_filename(name: str) -> str | None:
    import re
    s = re.sub(r"[^A-Za-z0-9_\- ]+", "", name).strip()
    s = s.replace(" ", "_").lower()
    return s or None


# ───────── WebSocket ─────────

@app.websocket("/live")
async def live(ws: WebSocket) -> None:
    token = ws.query_params.get("t")
    if not await auth.ws_check_token(ws, token):
        return
    device = ws.query_params.get("device", "tablet")
    await ws.accept()
    await hub.add(ws, device=device)

    # TouchDesigner client: route inbound JSON to td.on_message; skip
    # layout/theme/desktops bootstrap (TD doesn't render those).
    if device == "touchdesigner":
        td.register_ws(ws, asyncio.get_running_loop())
        td.resync_subscriptions()
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await td.on_message(msg)
        except WebSocketDisconnect:
            pass
        finally:
            td.unregister_ws(ws)
            await hub.remove(ws)
        return

    # Push the currently-resolved layout + theme + desktops to bootstrap UI.
    cfg = _resolve_layout()
    theme = _theme_for(cfg)
    if cfg:
        payload = json.dumps({"t": "layout", "layout": cfg, "theme": theme})
        await ws.send_text(payload)
        hub.clients[ws]["last_sent"] = payload
        hub.clients[ws]["cfg"] = cfg
        hub._refresh_textbox_subs(ws, hub.clients[ws], cfg)

    await ws.send_text(json.dumps({"t": "desktops", "desktops": desktops.list_desktops()}))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _handle(ws, msg)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)


async def _handle(ws: WebSocket, msg: dict) -> None:
    kind = msg.get("t")
    if kind == "press":
        wid = msg.get("id")
        cfg = hub.cfg_for(ws)
        widget = _find_widget(cfg, wid) if cfg else None
        if widget:
            action = (widget.get("props") or {}).get("action")
            actions.dispatch(action, payload=msg.get("payload"), context=_current_context, widget=widget)
            print(f"[press] {wid} -> {action}", flush=True)
        else:
            print(f"[press] unknown widget id: {wid}", flush=True)
    elif kind == "release":
        pass
    elif kind == "value":
        wid = msg.get("id")
        value = msg.get("value")
        cfg = hub.cfg_for(ws)
        widget = _find_widget(cfg, wid) if cfg else None
        if widget:
            action = (widget.get("props") or {}).get("action")
            payload = dict(msg.get("payload") or {})
            payload["value"] = value
            actions.dispatch(action, payload=payload, context=_current_context, widget=widget)
        else:
            print(f"[value] unknown widget id: {wid}", flush=True)
    elif kind == "input":
        wid = msg.get("id")
        text = msg.get("text")
        cfg = hub.cfg_for(ws)
        widget = _find_widget(cfg, wid) if cfg else None
        if widget:
            action = (widget.get("props") or {}).get("action")
            payload = {"value": text, "text": text}
            actions.dispatch(action, payload=payload, context=_current_context, widget=widget)
    elif kind == "focus_hwnd":
        hwnd = msg.get("hwnd")
        if hwnd is not None:
            actions.dispatch({"type": "focus_window", "hwnd": int(hwnd)})
    elif kind == "switch_desktop":
        idx = msg.get("index")
        if idx is not None:
            desktops.switch_to(int(idx))
    elif kind == "hello":
        pass
    elif kind == "preview":
        # editor → tablet preview: broadcast a transient layout to tablet devices
        layout = msg.get("layout")
        if layout:
            expanded = _expand_dynamic_widgets(layout)
            theme = themes.get((expanded.get("canvas") or {}).get("theme"))
            await hub.broadcast_layout(_augment_with_context(expanded), theme)
    else:
        print(f"[ws] unhandled: {kind}", flush=True)


def _find_widget(cfg: dict | None, wid: str):
    if not cfg:
        return None
    for w in cfg.get("widgets") or []:
        if w.get("id") == wid:
            return w
    return None


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"
