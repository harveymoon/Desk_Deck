"""Theme loader. Themes are CSS-variable bundles in themes/*.yaml."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

THEMES_DIR = Path(__file__).resolve().parent.parent / "themes"

# Built-in fallback used if no themes/ files exist.
_BUILTIN = {
    "name": "midnight",
    "vars": {
        "--bg": "#0a0a0c",
        "--surface": "#141418",
        "--surface-2": "#1c1c22",
        "--surface-3": "#24242c",
        "--border": "#2a2a32",
        "--border-hi": "#3a3a45",
        "--text": "#e6e6ea",
        "--text-dim": "#7a7a85",
        "--accent": "#5cf",
        "--danger": "#f55",
        "--font-ui": "Inter, system-ui, sans-serif",
        "--font-mono": "JetBrains Mono, ui-monospace, monospace",
        "--radius": "0px",
        "--border-w": "1px",
        "--gap": "8px",
    },
}

_cache: dict[str, dict[str, Any]] | None = None
_lock = threading.Lock()
_observer: Observer | None = None


def _read(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("name", path.stem)
    data.setdefault("vars", {})
    return data


def _invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def load_all() -> dict[str, dict[str, Any]]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        out: dict[str, dict[str, Any]] = {}
        if THEMES_DIR.exists():
            for p in sorted(THEMES_DIR.glob("*.yaml")):
                try:
                    t = _read(p)
                    out[t["name"]] = t
                except Exception as e:
                    print(f"[themes] failed to load {p.name}: {e}", flush=True)
        if not out:
            out[_BUILTIN["name"]] = _BUILTIN
        _cache = out
        return _cache


def get(name: str | None) -> dict[str, Any]:
    """Return the named theme, or the first available, or the built-in."""
    themes = load_all()
    if name and name in themes:
        return themes[name]
    if themes:
        return next(iter(themes.values()))
    return _BUILTIN


def names() -> list[str]:
    return list(load_all().keys())


# ───────── hot-reload ─────────

class _DebouncedHandler(FileSystemEventHandler):
    def __init__(self, on_change: Callable[[], None], delay: float = 0.15) -> None:
        self.on_change = on_change
        self.delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _is_yaml(self, path: str) -> bool:
        return path.endswith(".yaml") or path.endswith(".yml")

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        try:
            self.on_change()
        except Exception as e:
            print(f"[themes] reload callback failed: {e}", flush=True)

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if not self._is_yaml(str(event.src_path)):
            return
        self._schedule()


def watch(on_change: Callable[[], None]) -> None:
    global _observer
    if _observer is not None:
        return
    THEMES_DIR.mkdir(parents=True, exist_ok=True)

    def wrapped() -> None:
        _invalidate()
        on_change()

    handler = _DebouncedHandler(wrapped)
    _observer = Observer()
    _observer.schedule(handler, str(THEMES_DIR), recursive=False)
    _observer.daemon = True
    _observer.start()


def stop_watching() -> None:
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=1)
        _observer = None
