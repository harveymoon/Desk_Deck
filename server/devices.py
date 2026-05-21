"""Device-handler registry.

A "device" is a kind of client that connects to /live with a
?device=<name> query param. The tablet is one device; the
TouchDesigner connector is another. Plugins register their own device
handlers here at startup (via server.registry's register(api) hook),
so the WebSocket router in app.py doesn't have to know about specific
device types.

Handlers each get the raw FastAPI WebSocket plus the asyncio loop, and
implement three optional callbacks:

  on_open(ws, loop)              — called once after the WS accepts;
                                   typical: register the WS with the
                                   plugin's helper module, push initial
                                   state, etc.
  on_message(ws, msg, loop)      — called for every JSON message
                                   received from the device; msg is the
                                   already-parsed dict.
  on_close(ws, loop)             — called once when the WS disconnects;
                                   typical: clean up references.

If no handler is registered for a device name, the WebSocket falls
back to the built-in "tablet" path in app.py.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable

# Each entry: { "on_open": fn|None, "on_message": fn|None, "on_close": fn|None }
_handlers: dict[str, dict[str, Callable | None]] = {}
_lock = threading.Lock()


def register_device(name: str,
                    on_open: Callable | None = None,
                    on_message: Callable | None = None,
                    on_close: Callable | None = None) -> None:
    """Register a device handler. Last registration for a given name wins."""
    with _lock:
        _handlers[name] = {
            "on_open":    on_open,
            "on_message": on_message,
            "on_close":   on_close,
        }
    print(f"[devices] registered {name!r}", flush=True)


def has(name: str) -> bool:
    return name in _handlers


def names() -> list[str]:
    return sorted(_handlers.keys())


async def _maybe_await(result: Any) -> None:
    if asyncio.iscoroutine(result):
        await result


async def dispatch_open(name: str, ws, loop: asyncio.AbstractEventLoop) -> bool:
    """Call the on_open hook for `name`. Returns True if a handler ran,
    False if the device wasn't registered (caller should fall through
    to its default behaviour)."""
    h = _handlers.get(name)
    if not h or not h.get("on_open"):
        return name in _handlers   # registered but no on_open hook → still "handled"
    try:
        await _maybe_await(h["on_open"](ws, loop))
    except Exception as e:
        print(f"[devices] {name!r} on_open failed: {e}", flush=True)
    return True


async def dispatch_message(name: str, ws, msg: dict, loop: asyncio.AbstractEventLoop) -> None:
    h = _handlers.get(name)
    if not h or not h.get("on_message"):
        return
    try:
        await _maybe_await(h["on_message"](ws, msg, loop))
    except Exception as e:
        print(f"[devices] {name!r} on_message failed: {e}", flush=True)


async def dispatch_close(name: str, ws, loop: asyncio.AbstractEventLoop) -> None:
    h = _handlers.get(name)
    if not h or not h.get("on_close"):
        return
    try:
        await _maybe_await(h["on_close"](ws, loop))
    except Exception as e:
        print(f"[devices] {name!r} on_close failed: {e}", flush=True)
