"""Textbox source: mirrors TD's `ui.status` (the bottom-bar text)."""
from __future__ import annotations

from .. import td


def _format(payload: dict | None) -> str:
    if not payload:
        return "(status idle)\n"
    s = payload.get("text") or ""
    if not s:
        return "(status idle)\n"
    return s.strip() + "\n"


def subscribe(emit):
    def on_state(payload):
        emit(_format(payload), replace=True)
    cleanup = td.subscribe("status", on_state)
    emit(_format(td.state("status")), replace=True)
    return cleanup
