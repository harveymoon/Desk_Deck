"""Example slider value target: bumps the system volume in 1% steps via VK codes.

This is intentionally a no-op-on-error demo. For real volume control, install
pycaw and call its endpoint API directly.
"""
from __future__ import annotations


def on_value(value, widget=None, context=None):
    """Receives a 0..100 value. We don't have a real volume hook here, just log."""
    try:
        v = max(0, min(100, int(value)))
    except (TypeError, ValueError):
        v = 0
    print(f"[set_volume] target volume = {v}%", flush=True)
