"""Foreground window watcher.

Polls the Win32 foreground window at a low rate and invokes a callback when
the (process, title, class) tuple changes. Runs in a daemon thread.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import psutil
import win32gui
import win32process

POLL_INTERVAL = 0.25  # seconds; ~4 Hz is plenty for app switching

_thread: threading.Thread | None = None
_stop = threading.Event()


def _foreground() -> tuple[str, str, str, int]:
    """Return (process_name, window_title, window_class, pid) for the foreground window."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "", "", "", 0
        title = win32gui.GetWindowText(hwnd) or ""
        win_class = win32gui.GetClassName(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            process = psutil.Process(pid).name() if pid else ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process = ""
        return process, title, win_class, pid or 0
    except Exception:
        return "", "", "", 0


def start(on_change: Callable[[str, str, str, int], None]) -> None:
    """Start the watcher thread. on_change receives (process, title, class, pid)
    every time the foreground window changes. Safe to call once."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def loop() -> None:
        last: tuple[str, str, str, int] | None = None
        try:
            current = _foreground()
            on_change(*current)
            last = current
        except Exception as e:
            print(f"[watcher] initial read failed: {e}", flush=True)

        while not _stop.is_set():
            try:
                current = _foreground()
                if current != last:
                    last = current
                    on_change(*current)
            except Exception as e:
                print(f"[watcher] poll error: {e}", flush=True)
            _stop.wait(POLL_INTERVAL)

    _thread = threading.Thread(target=loop, daemon=True, name="dd-fg-watcher")
    _thread.start()


def stop() -> None:
    _stop.set()


def current() -> tuple[str, str, str, int]:
    """Synchronous read of the current foreground (used at startup)."""
    return _foreground()
