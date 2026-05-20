"""Provider auto-discovery.

A provider is a Python module under server/providers/ that may expose any of:
  - subscribe(emit): textbox source — call emit(text) to append. Returns cleanup fn.
  - on_value(value, widget, context): action target for sliders / python buttons.
  - on_press(widget, context): action target for buttons (alternative to on_value).
  - get_widgets(context): dynamic widget generator (not used by core yet).
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import threading
from pathlib import Path
from typing import Any, Callable

PROVIDERS_DIR = Path(__file__).resolve().parent / "providers"

_modules: dict[str, Any] = {}
_lock = threading.Lock()


def _discover() -> None:
    """Import every .py in server/providers/ (other than __init__)."""
    with _lock:
        _modules.clear()
        if not PROVIDERS_DIR.exists():
            return
        for finder, name, ispkg in pkgutil.iter_modules([str(PROVIDERS_DIR)]):
            if name.startswith("_"):
                continue
            try:
                mod = importlib.import_module(f"server.providers.{name}")
                _modules[name] = mod
            except Exception as e:
                print(f"[registry] failed to import provider {name}: {e}", flush=True)


def reload() -> None:
    _discover()


def get(name: str) -> Any:
    if not _modules:
        _discover()
    return _modules.get(name)


def has(name: str) -> bool:
    return get(name) is not None


def call_on_value(name: str, value: Any, widget: dict, context: dict) -> None:
    mod = get(name)
    if not mod:
        print(f"[registry] no provider named {name!r}", flush=True)
        return
    fn = getattr(mod, "on_value", None) or getattr(mod, "on_press", None)
    if not fn:
        print(f"[registry] provider {name!r} has no on_value/on_press", flush=True)
        return
    try:
        fn(value, widget, context) if fn.__code__.co_argcount >= 3 else fn(value)
    except Exception as e:
        print(f"[registry] provider {name!r} raised: {e}", flush=True)


def subscribe_to(name: str, emit: Callable[[str], None]) -> Callable[[], None] | None:
    mod = get(name)
    if not mod:
        return None
    fn = getattr(mod, "subscribe", None)
    if not fn:
        return None
    try:
        cleanup = fn(emit)
        return cleanup if callable(cleanup) else (lambda: None)
    except Exception as e:
        print(f"[registry] subscribe to {name!r} failed: {e}", flush=True)
        return None
