"""In-memory log tail: tees sys.stdout / sys.stderr into a ring buffer and
notifies async listeners line-by-line. Powers the editor's Logs tab via SSE.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections import deque
from typing import Any

MAX_LINES = 1000

_buffer: deque[dict[str, Any]] = deque(maxlen=MAX_LINES)
_lock = threading.Lock()
_seq = 0
# Async listeners are (loop, queue) tuples — _append_line uses
# loop.call_soon_threadsafe so writes from any thread are safe.
_listeners: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []


class _Tee:
    """Wrap a file-like object: write through AND mirror to the buffer."""

    def __init__(self, original, stream_name: str) -> None:
        self.original = original
        self.stream = stream_name
        self._partial = ""
        self._partial_lock = threading.Lock()

    def write(self, text: str) -> int:
        try:
            self.original.write(text)
        except Exception:
            pass
        if not text:
            return 0
        with self._partial_lock:
            self._partial += text
            while "\n" in self._partial:
                line, self._partial = self._partial.split("\n", 1)
                _append_line(line, self.stream)
        return len(text)

    def flush(self) -> None:
        try:
            self.original.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return self.original.isatty()
        except Exception:
            return False

    def fileno(self):
        return self.original.fileno()


def install() -> None:
    """Call once at process startup."""
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout, "out")
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr, "err")


def _append_line(line: str, stream: str = "out") -> None:
    global _seq
    entry = {
        "seq": _seq,
        "ts": time.strftime("%H:%M:%S"),
        "ms": int((time.time() % 1) * 1000),
        "stream": stream,
        "line": line,
    }
    with _lock:
        _seq += 1
        _buffer.append(entry)
        listeners = list(_listeners)
    for loop, q in listeners:
        try:
            loop.call_soon_threadsafe(_safe_put, q, entry)
        except RuntimeError:
            # loop already closed
            pass


def _safe_put(q: asyncio.Queue, entry: dict[str, Any]) -> None:
    try:
        q.put_nowait(entry)
    except asyncio.QueueFull:
        # Drop oldest to make room — log spam shouldn't deadlock the stream
        try:
            q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(entry)
        except Exception:
            pass


def snapshot() -> list[dict[str, Any]]:
    with _lock:
        return list(_buffer)


def add_listener(loop: asyncio.AbstractEventLoop, q: asyncio.Queue) -> None:
    with _lock:
        _listeners.append((loop, q))


def remove_listener(q: asyncio.Queue) -> None:
    with _lock:
        _listeners[:] = [(l, lq) for l, lq in _listeners if lq is not q]
