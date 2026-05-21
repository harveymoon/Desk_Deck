"""Textbox source: TouchDesigner performance HUD.

Renders as:
  FPS 59.9  ·  cook 1.4 ms  ·  GPU 384 MB
"""
from __future__ import annotations

from .. import td


def _format(payload: dict | None) -> str:
    if not payload:
        return "(td perf unavailable)\n"
    fps = payload.get("fps")
    cook = payload.get("cook_ms")
    gpu = payload.get("gpu_mem_mb")
    parts = []
    if fps is not None:
        parts.append(f"FPS {fps:.1f}" if isinstance(fps, (int, float)) else f"FPS {fps}")
    if cook is not None:
        parts.append(f"cook {cook:.1f} ms" if isinstance(cook, (int, float)) else f"cook {cook} ms")
    if gpu is not None:
        parts.append(f"GPU {int(gpu)} MB" if isinstance(gpu, (int, float)) else f"GPU {gpu} MB")
    return "  ·  ".join(parts) + "\n"


def subscribe(emit):
    def on_state(payload):
        emit(_format(payload), replace=True)

    cleanup = td.subscribe("perf", on_state)
    emit(_format(td.state("perf")), replace=True)
    return cleanup
