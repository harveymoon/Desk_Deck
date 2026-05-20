"""YAML config loader. Loads configs/*.yaml, exposes match(), and provides
a watchdog observer that calls a callback when files change (debounced)."""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


_cache: list[dict[str, Any]] | None = None
_cache_lock = threading.Lock()


def _read(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("name", path.stem)
    return data


def _invalidate() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def load_all() -> list[dict[str, Any]]:
    """Return all configs. Cached in memory; cache is invalidated on file change."""
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        if not CONFIG_DIR.exists():
            _cache = []
            return _cache
        out: list[dict[str, Any]] = []
        for p in sorted(CONFIG_DIR.glob("*.yaml")):
            # Skip underscore-prefixed files — those are non-layout configs
            # (e.g. _filters.yaml) that shouldn't appear in matchers or UI.
            if p.name.startswith("_"):
                continue
            try:
                out.append(_read(p))
            except Exception as e:
                print(f"[config] failed to load {p.name}: {e}", flush=True)
        _cache = out
        return _cache


def load_one(name: str) -> dict[str, Any] | None:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    try:
        return _read(path)
    except Exception as e:
        print(f"[config] failed to load {path.name}: {e}", flush=True)
        return None


def save(name: str, data: dict[str, Any]) -> None:
    """Write a config to configs/{name}.yaml. Invalidates the cache."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"{name}.yaml"
    # Strip transient/internal keys that shouldn't be persisted
    clean = {k: v for k, v in data.items() if not k.startswith("_") and k != "synthetic"}
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(clean, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
    _invalidate()


def delete(name: str) -> bool:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return False
    path.unlink()
    _invalidate()
    return True


def _matches(rule: dict[str, Any], process: str, title: str, win_class: str) -> bool:
    if "process" in rule and rule["process"].lower() != (process or "").lower():
        return False
    if "window_title_regex" in rule and not re.search(rule["window_title_regex"], title or ""):
        return False
    if "window_class" in rule and rule["window_class"] != (win_class or ""):
        return False
    return "process" in rule or "window_title_regex" in rule or "window_class" in rule


def match(process: str, title: str, win_class: str) -> dict[str, Any] | None:
    """Return first config whose match rule fits, else 'default' if present."""
    configs = load_all()
    default = None
    for cfg in configs:
        if cfg.get("name", "").lower() == "default":
            default = cfg
            continue
        rule = cfg.get("match") or {}
        if _matches(rule, process, title, win_class):
            return cfg
    return default


# ───────── Hot-reload ─────────

class _DebouncedHandler(FileSystemEventHandler):
    """Coalesce a burst of file events (write+close fires multiple) into one."""

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
            print(f"[config] hot-reload callback failed: {e}", flush=True)

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if not self._is_yaml(str(event.src_path)):
            return
        self._schedule()


_observer: Observer | None = None


def watch(on_change: Callable[[], None]) -> None:
    """Start watching CONFIG_DIR. Invalidates the cache and calls on_change()
    on any YAML add/edit/delete (debounced)."""
    global _observer
    if _observer is not None:
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def wrapped() -> None:
        _invalidate()
        on_change()

    handler = _DebouncedHandler(wrapped)
    _observer = Observer()
    _observer.schedule(handler, str(CONFIG_DIR), recursive=False)
    _observer.daemon = True
    _observer.start()


def stop_watching() -> None:
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=1)
        _observer = None
