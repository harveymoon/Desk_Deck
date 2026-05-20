"""App filter for the Apps overlay — hide invisible/special apps.

Stored in configs/_filters.yaml. The underscore prefix keeps it out of the
configs dropdown / matcher (config.load_all skips _-prefixed files).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import yaml

FILTERS_PATH = Path(__file__).resolve().parent.parent / "configs" / "_filters.yaml"

DEFAULT: dict[str, Any] = {
    "hide_processes": [
        "NVIDIA Overlay.exe",
        "NVIDIA Share.exe",
        "TextInputHost.exe",
        "SearchHost.exe",
        "StartMenuExperienceHost.exe",
        "ShellExperienceHost.exe",
    ],
    "hide_classes": [
        "Progman",
        "Shell_TrayWnd",
        "WorkerW",
    ],
    "hide_title_regex": [
        "^Program Manager$",
    ],
}

_cache: dict[str, Any] | None = None
_lock = threading.Lock()


def _read() -> dict[str, Any]:
    if not FILTERS_PATH.exists():
        return _deepcopy(DEFAULT)
    try:
        with FILTERS_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        out = _deepcopy(DEFAULT)
        for k in ("hide_processes", "hide_classes", "hide_title_regex"):
            if k in data:
                out[k] = list(data[k] or [])
        return out
    except Exception as e:
        print(f"[filters] load failed: {e}", flush=True)
        return _deepcopy(DEFAULT)


def _deepcopy(d: dict[str, Any]) -> dict[str, Any]:
    return {k: list(v) for k, v in d.items()}


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def load() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _read()
        return _deepcopy(_cache)


def save(data: dict[str, Any]) -> None:
    FILTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        "hide_processes": [str(s) for s in (data.get("hide_processes") or [])],
        "hide_classes":   [str(s) for s in (data.get("hide_classes") or [])],
        "hide_title_regex": [str(s) for s in (data.get("hide_title_regex") or [])],
    }
    with FILTERS_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(clean, f, sort_keys=False, allow_unicode=True)
    invalidate()


def is_hidden(process: str, title: str, win_class: str) -> bool:
    f = load()
    procs = {p.lower() for p in f.get("hide_processes", [])}
    if (process or "").lower() in procs:
        return True
    if win_class in set(f.get("hide_classes", [])):
        return True
    for pat in f.get("hide_title_regex", []):
        try:
            if re.search(pat, title or ""):
                return True
        except re.error:
            continue
    return False
