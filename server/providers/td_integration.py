"""TouchDesigner integration — single registration entry point.

This is the canonical example of the plugin contract:

  - register(api) is called once at server startup (by server.registry)
    with a small API surface for adding actions, device handlers, and
    startup hooks.
  - This module is the ONLY place that's allowed to import
    server.td. Core files (server.app, server.actions, web/shared/
    widgets.js) must not reference TouchDesigner-specific code.

Adds:
  device  : "touchdesigner"  — the WS handler that bridges
            ws.receive_text() → td.on_message().
  actions : td_set_par, td_nudge_par, td_macro, td_toggle_par,
            td_open_help. Used by buttons/sliders/value-ladders
            in configs/touchdesigner.yaml.
  startup : three td.subscribe() hooks that push selected/rollover
            state to the tablet's td_pars panel + ladder + slider.

Textbox sources (td_selected, td_rollover, td_perf, td_status,
td_textport) live in their own provider files alongside this one and
are auto-discovered by server.registry — no registration needed here.

See ARCHITECTURE.md for the plugin contract.
"""
from __future__ import annotations

import asyncio
import json
import webbrowser
from typing import Any

from .. import td


def _hub():
    """Lazy hub accessor — server.app imports providers (via registry)
    during its module load, so a top-level `from ..app import hub`
    would close a circular import. Calling _hub() at push time gets
    around it cleanly."""
    from ..app import hub
    return hub


# ─── Startup hooks (called once by app.py after registry.reload) ────────

# Captured once the first startup hook runs — used by the rollover/
# selected push hooks below to schedule coroutines on the right loop.
_loop: asyncio.AbstractEventLoop | None = None


async def _on_startup(loop: asyncio.AbstractEventLoop) -> None:
    """Subscribe TD state hooks. Called by the core after the asyncio
    loop is running and registry.reload() has imported every provider."""
    global _loop
    _loop = loop
    td.subscribe("rollover", _on_td_rollover)
    td.subscribe("selected", _on_td_selected)
    td.subscribe("rollover", _on_td_rollover_for_highlight)


# ─── State-push handlers (run on the td.subscribe pub/sub thread) ──────

def _on_td_rollover(payload: dict | None) -> None:
    """Show / hide the rollover control widgets based on what's under the
    mouse in TD. Single-par hovers drive the slider / ladder / toggle.
    Anything else (pargroup, page, op, panel, nothing) hides them — they
    will get their own widgets when we add picker / page-tab / etc."""
    if _loop is None:
        return
    kind_of = (payload or {}).get("kind_of") or "none"
    is_par_hover = (kind_of == "par")
    p = ((payload or {}).get("par") or {}) if is_par_hover else {}
    style = p.get("style")
    val = p.get("value")

    is_toggle  = is_par_hover and (style == "Toggle")
    is_numeric = is_par_hover and (style in ("Float", "Int"))
    no_par     = not is_par_hover

    # Value ladder: hidden unless we have a numeric par to nudge.
    asyncio.run_coroutine_threadsafe(
        _hub().push_widget_update("td_rollover_ladder",
                               {"hidden": not is_numeric or no_par}),
        _loop,
    )

    # Slider (td_rollover_drive) — runs in REAL par-units, not 0..1.
    # Retune the slider's min/max/step/label to match the par so the
    # displayed value next to the bar is literally par.eval() and the
    # outgoing drag value is sent straight to TD (no normalisation).
    slider_patch: dict = {"hidden": not is_numeric or no_par}
    if is_numeric and val is not None:
        try:
            v = float(val) if not isinstance(val, bool) else (1.0 if val else 0.0)
        except (TypeError, ValueError):
            v = None
        if v is not None:
            nmin = float(p.get("normMin") or 0.0)
            nmax = float(p.get("normMax") or 1.0)
            if nmax <= nmin:
                nmin, nmax = (v - 1.0, v + 1.0) if v else (0.0, 1.0)
            # normMin/normMax are TD's *soft* UI range. The actual par
            # value can live outside it (e.g. trail.wlength=60 with
            # normMax=10). Expand the slider range so the thumb lands at
            # the real value's position. Hard clamps (clampMin/clampMax)
            # still cap the range.
            lo = min(nmin, v)
            hi = max(nmax, v)
            cmin = p.get("clampMin"); cmax = p.get("clampMax")
            if cmin is not None:
                try: lo = max(lo, float(cmin))
                except (TypeError, ValueError): pass
            if cmax is not None:
                try: hi = min(hi, float(cmax))
                except (TypeError, ValueError): pass
            if hi <= lo:
                hi = lo + max(abs(lo) * 0.01, 1.0)
            span = hi - lo
            if p.get("style") == "Int":
                step = 1
            else:
                import math as _math
                raw = span / 1000.0
                exp = _math.floor(_math.log10(raw)) if raw > 0 else -3
                step = max(10 ** exp, 1e-4)
            label = (p.get("name") or "VAL").upper()
            slider_patch.update({
                "min":   lo,
                "max":   hi,
                "step":  step,
                "label": label,
                "value": v,
            })
    asyncio.run_coroutine_threadsafe(
        _hub().push_widget_update("td_rollover_drive", slider_patch),
        _loop,
    )

    # Toggle (td_rollover_toggle)
    is_on = bool(val) if (is_toggle and val is not None) else False
    label = (f"{p.get('name','?').upper()}: {'ON' if is_on else 'OFF'}"
             if is_toggle else "(no toggle)")
    asyncio.run_coroutine_threadsafe(
        _hub().push_widget_update("td_rollover_toggle",
                               {"active": is_on, "hidden": not is_toggle or no_par,
                                "label": label}),
        _loop,
    )


def _on_td_selected(payload: dict | None) -> None:
    """Push a fresh op + pages snapshot to the td_pars param panel
    whenever TD reports a new selection (or a re-emit after a tablet
    edit). Hides the panel when nothing is selected."""
    if _loop is None:
        return
    ops = ((payload or {}).get("ops") or [])
    pages = ((payload or {}).get("pages") or [])
    if not ops:
        asyncio.run_coroutine_threadsafe(
            _hub().push_widget_update("td_pars", {"op": None, "pages": [], "hidden": False}),
            _loop,
        )
        return
    asyncio.run_coroutine_threadsafe(
        _hub().push_widget_update("td_pars", {
            "op": ops[0],
            "pages": pages,
            "hidden": False,
        }),
        _loop,
    )


def _on_td_rollover_for_highlight(payload: dict | None) -> None:
    """Highlight the par-panel row corresponding to the par under the
    mouse, but only when it lives on the currently-displayed op."""
    if _loop is None:
        return
    if (payload or {}).get("kind_of") != "par":
        asyncio.run_coroutine_threadsafe(
            _hub().push_widget_update("td_pars", {"highlight": None}),
            _loop,
        )
        return
    op_ = (payload.get("op") or {})
    par = (payload.get("par") or {})
    sel = td.state("selected") or {}
    sel_ops = sel.get("ops") or []
    sel_path = sel_ops[0].get("path") if sel_ops else None
    if not sel_path or sel_path != op_.get("path"):
        return
    asyncio.run_coroutine_threadsafe(
        _hub().push_widget_update("td_pars", {"highlight": par.get("name")}),
        _loop,
    )


# ─── Action handlers ────────────────────────────────────────────────────

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

    If payload.nudge is set (from an inline value-ladder in the param
    panel), routes to _td_nudge_par instead so the value is treated as
    a delta. payload.path/par win over action.path/par so smart widgets
    can target any par on the fly. $rollover sentinel resolves at
    dispatch time from td.state('rollover')."""
    if payload and payload.get("nudge"):
        return _td_nudge_par(action, payload, context, widget)
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

    payload.value carries the delta. Three resolution paths:
      1. Explicit path+par from payload — value looked up in
         td.state('selected').pages.
      2. $rollover sentinel — current value from rollover state.
      3. Implicit (no path/par) — same as $rollover.
    Honours Int by rounding. Falls back to 0 if no cached current value."""
    path  = (payload.get("path")  if payload else None) or action.get("path") or ""
    par   = (payload.get("par")   if payload else None) or action.get("par")  or ""
    style = (payload.get("style") if payload else None) or None

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
        style = style or p_meta.get("style")
    else:
        sel = td.state("selected") or {}
        for page in (sel.get("pages") or []):
            for p in (page.get("pars") or []):
                if p.get("name") == par:
                    cur_val = p.get("value")
                    style = style or p.get("style")
                    break
            if cur_val is not None:
                break

    if cur_val is None:
        print(f"[actions] td_nudge_par: no cached current value for {path}.{par} — sending raw delta", flush=True)
        cur_val = 0
    try:
        new_val = float(cur_val) + delta
    except (TypeError, ValueError):
        return
    if style == "Int":
        new_val = int(round(new_val))
    td.send_cmd("set_par", path=path, par=par, value=new_val)


def _td_toggle_par(action: dict[str, Any], payload: dict[str, Any], context: dict, widget: dict) -> None:
    """Flip a Toggle-style parameter. With no path/par, targets the par
    currently under the mouse in TD (kind_of=='par')."""
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


# ─── Device handler ─────────────────────────────────────────────────────

async def _on_device_open(ws, loop: asyncio.AbstractEventLoop) -> None:
    td.register_ws(ws, loop)
    td.resync_subscriptions()


async def _on_device_message(ws, msg: dict, loop: asyncio.AbstractEventLoop) -> None:
    await td.on_message(msg)


async def _on_device_close(ws, loop: asyncio.AbstractEventLoop) -> None:
    td.unregister_ws(ws)


# ─── Plugin entry point ─────────────────────────────────────────────────

def register(api) -> None:
    api.register_action("td_set_par",    _td_set_par)
    api.register_action("td_nudge_par",  _td_nudge_par)
    api.register_action("td_macro",      _td_macro)
    api.register_action("td_toggle_par", _td_toggle_par)
    api.register_action("td_open_help",  _td_open_help)

    api.register_device("touchdesigner",
                        on_open=_on_device_open,
                        on_message=_on_device_message,
                        on_close=_on_device_close)

    api.register_startup_hook(_on_startup)
