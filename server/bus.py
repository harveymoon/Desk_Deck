"""Tiny synchronous pub/sub for in-process events (widget updates, context changes)."""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable

_subs: dict[str, list[Callable]] = defaultdict(list)
_lock = threading.Lock()


def subscribe(topic: str, fn: Callable) -> Callable[[], None]:
    with _lock:
        _subs[topic].append(fn)

    def unsubscribe() -> None:
        with _lock:
            try:
                _subs[topic].remove(fn)
            except ValueError:
                pass

    return unsubscribe


def publish(topic: str, *args, **kwargs) -> None:
    with _lock:
        listeners = list(_subs.get(topic, ()))
    for fn in listeners:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            print(f"[bus] subscriber error on {topic}: {e}")
