"""Textbox source: parameter currently under the mouse in TouchDesigner.

Renders as:
  amp = 0.50  [0..1]  · noise1.amp (Float)

When the mouse isn't over a parameter: "(hover a parameter in TD)".
"""
from __future__ import annotations

from .. import td


def _format(payload: dict | None) -> str:
    if not payload:
        return "(hover a parameter in TD)\n"
    par = payload.get("par") or {}
    op_ = payload.get("op") or {}
    name = par.get("name", "?")
    val_eval = par.get("value")
    val_raw  = par.get("val")
    style = par.get("style", "?")
    nmin = par.get("normMin")
    nmax = par.get("normMax")
    cmin = par.get("clampMin")
    cmax = par.get("clampMax")

    # Show eval() value; if the raw typed value differs (expression mode),
    # surface it in parens so the user can tell.
    if val_raw is not None and val_raw != val_eval and not isinstance(val_raw, (int, float, bool)):
        val_s = f"{_v(val_eval)}  (raw='{val_raw}')"
    else:
        val_s = _v(val_eval)

    # Build the range hint. Prefer clamp if set, else normalised UI range.
    range_s = ""
    if cmin is not None or cmax is not None:
        range_s = f"  clamp[{_n(cmin)}..{_n(cmax)}]"
    elif nmin is not None and nmax is not None:
        range_s = f"  norm[{_n(nmin)}..{_n(nmax)}]"

    op_name = op_.get("name", "?")
    return f"{name} = {val_s}{range_s}  ·  {op_name}.{name} ({style})\n"


def _n(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def _v(v):
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def subscribe(emit):
    def on_state(payload):
        emit(_format(payload), replace=True)

    cleanup = td.subscribe("rollover_par", on_state)
    emit(_format(td.state("rollover_par")), replace=True)
    return cleanup
