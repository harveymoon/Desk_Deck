"""Provider auto-discovery.

A provider is a Python module under server/providers/ that may expose any of:
  - subscribe(emit): textbox source — call emit(text) to append. Returns cleanup fn.
  - on_value(value, widget, context): action target for sliders / python buttons.
  - on_press(widget, context): action target for buttons (alternative to on_value).
  - get_widgets(context): dynamic widget generator (not used by core yet).
  - register(api): plugin registration entry point — called once at
    server startup. The api object exposes:
        api.register_action(type_name, fn)
        api.register_device(name, on_open=, on_message=, on_close=)
        api.register_startup_hook(fn)   # async fn(loop) called after registry.reload
    All registrations are optional. See ARCHITECTURE.md for the contract.
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

PROVIDERS_DIR = Path(__file__).resolve().parent / "providers"

_modules: dict[str, Any] = {}
_lock = threading.Lock()

# Startup hooks registered by plugins. Drained by app.py after
# registry.reload() — see _on_startup.
_startup_hooks: list[Callable[..., Awaitable[None] | None]] = []


class _PluginAPI:
    """The small surface a plugin's register(api) call gets handed.

    Kept intentionally tight: actions, device handlers, startup hooks.
    Anything else (textbox sources, on_value/on_press) is discovered
    by module-attribute scan and doesn't need an explicit call.

    Imports are deferred to method-call time to avoid a circular import
    with server.actions (which itself imports server.registry)."""

    @staticmethod
    def register_action(type_name: str, fn: Callable) -> None:
        from . import actions
        actions.register_action(type_name, fn)

    @staticmethod
    def register_device(name: str,
                        on_open: Callable | None = None,
                        on_message: Callable | None = None,
                        on_close: Callable | None = None) -> None:
        from . import devices
        devices.register_device(name, on_open=on_open,
                                on_message=on_message, on_close=on_close)

    @staticmethod
    def register_startup_hook(fn: Callable) -> None:
        _startup_hooks.append(fn)


def take_startup_hooks() -> list[Callable]:
    """Pop and return all registered startup hooks. Called by app.py
    once during @app.on_event('startup') after registry.reload()."""
    hooks = list(_startup_hooks)
    _startup_hooks.clear()
    return hooks


def _discover() -> None:
    """Import every .py in server/providers/ (other than __init__).

    After import, if a module defines register(api), invoke it so the
    plugin can register actions, device handlers, and startup hooks
    via the api surface."""
    with _lock:
        _modules.clear()
        _startup_hooks.clear()
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
                continue
            reg = getattr(mod, "register", None)
            if callable(reg):
                try:
                    reg(_PluginAPI)
                except Exception as e:
                    print(f"[registry] register() failed for {name}: {e}", flush=True)


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
