"""Textbox source: currently selected operator(s) in TouchDesigner.

Renders as one line per selected op:
  SELECTED · noise1 (Noise CHOP) · /proj/noise1

Empty selection → "(no selection)".
"""
from __future__ import annotations

from .. import td


def _pretty(op_type: str, family: str) -> str:
    """noiseCHOP -> Noise CHOP. Defensive: returns op_type unchanged if it
    doesn't fit the convention."""
    if family and op_type.endswith(family):
        head = op_type[: -len(family)]
        if head:
            return f"{head[:1].upper()}{head[1:]} {family.upper()}"
    return op_type


def _format(state: dict | None) -> str:
    ops = (state or {}).get("ops") or []
    if not ops:
        return "(no selection)\n"
    lines = []
    for o in ops:
        lines.append(
            f"SELECTED · {o.get('name','?')} "
            f"({_pretty(o.get('type',''), o.get('family',''))}) · "
            f"{o.get('path','?')}"
        )
    return "\n".join(lines) + "\n"


def subscribe(emit):
    def on_state(payload):
        emit(_format(payload), replace=True)

    cleanup = td.subscribe("selected", on_state)
    # Render whatever's cached (or the empty placeholder)
    emit(_format(td.state("selected")), replace=True)
    return cleanup
