"""Sidebar config — list of buttons rendered on the tablet's right-side bar.

Stored in configs/_sidebar.yaml. Each button:
  - id     (stable string)
  - glyph  (single char / emoji shown big)
  - label  (short text shown under the glyph)
  - kind   "overlay" | "action"
  - target (for overlay: built-in name; for action: an action dict)

Built-in overlays: "apps", "winri", "bookmarks".

If the YAML is missing or malformed, we return DEFAULT so the tablet
always has *something* clickable.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parent.parent / "configs" / "_sidebar.yaml"

DEFAULT: dict[str, Any] = {
    "buttons": [
        {"id": "apps",      "glyph": "▦",  "label": "Apps",      "kind": "overlay", "target": "apps"},
        {"id": "winri",     "glyph": "⊞",  "label": "Winri",     "kind": "overlay", "target": "winri"},
        {"id": "favorites", "glyph": "★",  "label": "Favorites", "kind": "overlay", "target": "bookmarks"},
        {"id": "dashboard", "glyph": "📊", "label": "Dashboard", "kind": "action",
         "target": {"type": "hotkey", "keys": "ctrl+shift+g"}},
    ]
}

_cache: dict[str, Any] | None = None
_lock = threading.Lock()


def _read() -> dict[str, Any]:
    if not PATH.exists():
        return {"buttons": list(DEFAULT["buttons"])}
    try:
        with PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        out = {"buttons": []}
        for b in data.get("buttons", []) or []:
            if not isinstance(b, dict):
                continue
            out["buttons"].append(_normalize(b))
        if not out["buttons"]:
            out["buttons"] = list(DEFAULT["buttons"])
        return out
    except Exception as e:
        print(f"[sidebar] load failed: {e}", flush=True)
        return {"buttons": list(DEFAULT["buttons"])}


def _normalize(b: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id":     str(b.get("id") or "btn"),
        "glyph":  str(b.get("glyph") or "•"),
        "label":  str(b.get("label") or ""),
        "kind":   str(b.get("kind") or "overlay"),
        "target": b.get("target"),
    }
    return out


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def load() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _read()
        # Defensive copy so callers can't mutate the cache
        return {"buttons": [dict(b) for b in _cache["buttons"]]}


def save(data: dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    buttons = []
    for b in (data.get("buttons") or []):
        if isinstance(b, dict):
            buttons.append(_normalize(b))
    with PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"buttons": buttons}, f, sort_keys=False, allow_unicode=True)
    invalidate()
