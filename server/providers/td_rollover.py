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
    val = par.get("value")
    style = par.get("style", "?")
    nmin = par.get("normMin")
    nmax = par.get("normMax")
    range_s = ""
    if nmin is not None and nmax is not None:
        range_s = f"  [{_n(nmin)}..{_n(nmax)}]"
    val_s = _v(val)
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
