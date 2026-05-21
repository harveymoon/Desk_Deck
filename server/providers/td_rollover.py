"""Textbox source: whatever is currently under the mouse in TouchDesigner.

TD's ui.rollover returns one of: Par | ParGroup | Page | OP | PanelCOMP
(priority order). The connector classifies it and emits a single
state.rollover message; we format a one-line summary per kind so the
tablet textbox shows useful context whatever the user is hovering.

Examples by kind:
  par       amp = 0.50  norm[0..1]  ·  noise1.amp (Float)
  pargroup  color = (1.00, 0.50, 0.25) #FF8040  ·  constant1.color (RGB)
  page      Page 'Common' (12 pars)  ·  noise1
  op        OP: noise1 (Noise CHOP)  ·  /proj/noise1
  panel     Panel: container1 (Container COMP)  ·  /proj/container1
  none      (hover over something in TD)
"""
from __future__ import annotations

from .. import td


def _format(payload: dict | None) -> str:
    if not payload:
        return "(hover over something in TD)\n"
    kind_of = payload.get("kind_of") or "none"
    op_ = payload.get("op") or {}
    op_name = op_.get("name") or "?"
    op_type = op_.get("type") or ""
    op_path = op_.get("path") or ""

    if kind_of == "par":
        return _format_par(payload, op_name)

    if kind_of == "pargroup":
        return _format_pargroup(payload, op_name)

    if kind_of == "page":
        pg = payload.get("page") or {}
        label = pg.get("label") or pg.get("name") or "?"
        count = pg.get("par_count")
        count_s = f" ({count} pars)" if count is not None else ""
        return f"Page '{label}'{count_s}  ·  {op_name}\n"

    if kind_of == "op":
        return f"OP: {op_name} ({op_type})  ·  {op_path}\n"

    if kind_of == "panel":
        return f"Panel: {op_name} ({op_type})  ·  {op_path}\n"

    return "(hover over something in TD)\n"


def _format_par(payload, op_name):
    par = payload.get("par") or {}
    name = par.get("name", "?")
    val_eval = par.get("value")
    val_raw  = par.get("val")
    style = par.get("style", "?")
    nmin = par.get("normMin")
    nmax = par.get("normMax")
    cmin = par.get("clampMin")
    cmax = par.get("clampMax")

    if val_raw is not None and val_raw != val_eval and not isinstance(val_raw, (int, float, bool)):
        val_s = f"{_v(val_eval)}  (raw='{val_raw}')"
    else:
        val_s = _v(val_eval)

    range_s = ""
    if cmin is not None or cmax is not None:
        range_s = f"  clamp[{_n(cmin)}..{_n(cmax)}]"
    elif nmin is not None and nmax is not None:
        range_s = f"  norm[{_n(nmin)}..{_n(nmax)}]"
    return f"{name} = {val_s}{range_s}  ·  {op_name}.{name} ({style})\n"


def _format_pargroup(payload, op_name):
    pg = payload.get("pargroup") or {}
    name = pg.get("name") or "?"
    style = pg.get("style") or "?"
    values = pg.get("values") or []
    # Color pargroups: show the channel values and a hex swatch so the
    # readout is glanceable. The pargroup snapshot already includes
    # is_color + hex when style is RGB / RGBA.
    if pg.get("is_color"):
        chan_s = ", ".join(_v(v) for v in values)
        hex_s = pg.get("hex") or ""
        return f"{name} = ({chan_s}) {hex_s}  ·  {op_name}.{name} ({style})\n"
    chan_s = ", ".join(_v(v) for v in values) if values else ""
    paren = f"({chan_s})" if chan_s else ""
    return f"{name} = {paren}  ·  {op_name}.{name} ({style})\n"


def _n(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def _v(v):
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def subscribe(emit):
    def on_state(payload):
        emit(_format(payload), replace=True)

    cleanup = td.subscribe("rollover", on_state)
    emit(_format(td.state("rollover")), replace=True)
    return cleanup
