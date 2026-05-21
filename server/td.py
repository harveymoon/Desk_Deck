"""TouchDesigner bridge.

A single TouchDesigner instance opens one WebSocket to /live with
?device=touchdesigner&t=<token>. We:

  - track the latest state snapshot per kind (selected / rollover_par /
    perf / pane_path) in a small in-memory dict;
  - let provider modules (server/providers/td_*.py) subscribe to those
    kinds and push formatted text into tablet textboxes;
  - send typed commands back to TD on demand (set_par, pulse, focus_op,
    macro, subscribe, unsubscribe) for action handlers to use.

Only one TD connection is active at a time. Opening a second connection
replaces the first (typical when the user reloads Desk_Deck.tox).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Callable

# Public surface ------------------------------------------------------------

# `state` is keyed by message "kind" — selected | rollover_op | rollover_par
# | perf | pane_path. Always present (may be None or {}).
_state: dict[str, Any] = {
    "selected":     None,
    "rollover_op":  None,
    "rollover_par": None,
    "perf":         None,
    "pane_path":    None,
    "hello":        None,  # most recent hello from TD
}

# Subscribers per kind: each is a list of callables that take the new payload.
_subscribers: dict[str, list[Callable[[Any], None]]] = {}
_sub_lock = threading.Lock()

# The single active TD WebSocket and the loop it lives on.
_ws = None
_loop: asyncio.AbstractEventLoop | None = None

_next_id = 1
_id_lock = threading.Lock()
_last_seen_ts: float = 0.0
_pending_acks: dict[int, dict] = {}  # id -> the cmd we sent (kept briefly for logs)


# ---- connection lifecycle (called from server/app.py) ----

def register_ws(ws, loop: asyncio.AbstractEventLoop) -> None:
    global _ws, _loop
    if _ws is not None and _ws is not ws:
        try:
            # Close any prior connection so we don't double-send. The new
            # client wins.
            asyncio.run_coroutine_threadsafe(_ws.close(), _loop or loop)
        except Exception:
            pass
    _ws = ws
    _loop = loop
    print("[td] connection registered", flush=True)


def unregister_ws(ws) -> None:
    global _ws
    if _ws is ws:
        _ws = None
        print("[td] connection released", flush=True)


def connected() -> bool:
    return _ws is not None


def last_seen() -> float:
    return _last_seen_ts


# ---- inbound (called from the WS receive loop) ----

async def on_message(msg: dict) -> None:
    """Process one parsed JSON message from TD."""
    global _last_seen_ts
    _last_seen_ts = time.time()
    t = msg.get("t")
    if t == "hello":
        _state["hello"] = msg
        print(f"[td] hello · {msg.get('project') or '(no project)'} · "
              f"version {msg.get('td_version') or '?'}", flush=True)
        # Treat every hello as "the connector restarted; resync me." This
        # covers both first-connect AND TD's Reset Extensions case, where
        # the .tox Python class is re-instantiated (clearing its _subs set)
        # without the underlying WebSocket closing — without this, the
        # connector goes silent until the next actual reconnect.
        resync_subscriptions()
        _notify("hello", msg)
        return
    if t == "state":
        kind = msg.get("kind")
        if not kind:
            return
        _state[kind] = msg
        _notify(kind, msg)
        return
    if t == "log":
        line = msg.get("line", "")
        _notify("log", line)
        return
    if t == "ack":
        cid = msg.get("id")
        original = _pending_acks.pop(cid, None) if cid is not None else None
        if not msg.get("ok"):
            print(f"[td] ack#{cid} error: {msg.get('error')} (cmd={original})", flush=True)
        return
    print(f"[td] unhandled inbound t={t}", flush=True)


def _notify(kind: str, payload: Any) -> None:
    with _sub_lock:
        subs = list(_subscribers.get(kind, ()))
    for fn in subs:
        try:
            fn(payload)
        except Exception as e:
            print(f"[td] subscriber for {kind!r} raised: {e}", flush=True)


# ---- outbound (called from action handlers, providers) ----

def send_cmd(kind: str, **fields) -> int | None:
    """Send a typed command to TD. Returns the message id (for ack matching),
    or None if TD isn't connected.

    High-rate fire-and-forget kinds (set_par, pulse) skip the ack
    bookkeeping to keep slider drags from doubling WS traffic — the TD
    side also omits the ack reply for these. If a set_par silently fails
    the user will see it (param doesn't move), and we get one printed
    error from the TD side which is enough for diagnosis.
    """
    global _next_id
    if _ws is None or _loop is None:
        print(f"[td] no connection — dropping cmd {kind} {fields}", flush=True)
        return None
    with _id_lock:
        cid = _next_id
        _next_id += 1
    msg = {"t": "cmd", "id": cid, "kind": kind, **fields}
    if kind not in _FIRE_AND_FORGET:
        _pending_acks[cid] = msg
    try:
        asyncio.run_coroutine_threadsafe(_ws.send_text(json.dumps(msg)), _loop)
    except Exception as e:
        print(f"[td] send failed: {e}", flush=True)
        _pending_acks.pop(cid, None)
        return None
    return cid


_FIRE_AND_FORGET = {"set_par", "pulse"}


# ---- subscribe / state access (used by providers) ----

def subscribe(kind: str, fn: Callable[[Any], None]) -> Callable[[], None]:
    """Subscribe to state.<kind> or 'log' events. Returns an unsubscribe fn.

    fn(payload) is called every time TD pushes a fresh snapshot of that
    kind. If TD already has cached state, fn is invoked once immediately
    with that snapshot so the textbox doesn't sit blank on startup.
    """
    with _sub_lock:
        _subscribers.setdefault(kind, []).append(fn)

    cached = _state.get(kind)
    if cached is not None:
        try:
            fn(cached)
        except Exception:
            pass

    # When the first subscriber for a kind appears, ask TD to start
    # streaming it. Cheap optimization so TD only emits what's being read.
    if connected() and len(_subscribers[kind]) == 1 and kind in _STREAMABLE:
        send_cmd("subscribe", what=kind)

    def cleanup() -> None:
        with _sub_lock:
            lst = _subscribers.get(kind, [])
            try:
                lst.remove(fn)
            except ValueError:
                pass
            empty = not lst
        if empty and connected() and kind in _STREAMABLE:
            send_cmd("unsubscribe", what=kind)

    return cleanup


# Kinds TD can be told to start/stop streaming. (`hello` and `log` are
# always pushed when relevant.)
_STREAMABLE = {"selected", "rollover_op", "rollover_par", "perf", "pane_path"}


def state(kind: str) -> Any:
    """Latest cached snapshot for a kind, or None."""
    return _state.get(kind)


# ---- on-connect: ask TD to resume any active streams ----

def resync_subscriptions() -> None:
    """Called after a fresh TD connection — re-ask TD for everything any
    provider currently subscribes to (subscriptions are stable across TD
    reconnects, but TD itself has no memory of prior `subscribe` cmds)."""
    with _sub_lock:
        active = [k for k, lst in _subscribers.items() if lst and k in _STREAMABLE]
    for k in active:
        send_cmd("subscribe", what=k)
