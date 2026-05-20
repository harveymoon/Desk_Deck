"""Discord status provider — polls Discord.exe window titles.

Discord's window title encodes the unread badge count as "(N) ..." at the
start (e.g. "(3) #general | My Server - Discord"). We poll all visible
Discord windows every 2 s and emit a status snapshot using replace
semantics so the textbox always shows the current state, not a history.

No Discord account / RPC / OAuth needed — purely local window inspection.
For richer integration (real-time message ping events), see a future
Discord RPC provider.
"""
from __future__ import annotations

import re
import threading
import time

import psutil
import win32gui
import win32process

POLL_INTERVAL = 2.0
PROCESS_NAME = "Discord.exe"
UNREAD_RE = re.compile(r"^\((\d+)\)\s*")
# Trailing " - Discord" / " — Discord" suffix
SUFFIX_RE = re.compile(r"\s*[-—]\s*Discord\s*$")


def _discord_pids() -> set[int]:
    out: set[int] = set()
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() == PROCESS_NAME.lower():
                out.add(p.pid)
        except Exception:
            continue
    return out


def _list_discord_windows() -> list[dict]:
    pids = _discord_pids()
    if not pids:
        return []
    out: list[dict] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetParent(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if pid in pids:
            out.append({"hwnd": int(hwnd), "title": title})

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return out


def _format(windows: list[dict]) -> str:
    if not windows:
        return "Discord not running\n"
    total_unread = 0
    rows: list[str] = []
    for w in windows:
        title = w["title"]
        m = UNREAD_RE.match(title)
        unread = int(m.group(1)) if m else 0
        total_unread += unread
        rest = UNREAD_RE.sub("", title)
        rest = SUFFIX_RE.sub("", rest).strip()
        if not rest:
            rest = "Discord"
        marker = "●" if unread else " "
        if unread:
            rows.append(f"{marker} ({unread:>2}) {rest}")
        else:
            rows.append(f"{marker}       {rest}")

    header = f"DISCORD · {total_unread} unread · {len(windows)} window{'s' if len(windows) != 1 else ''}"
    ts = time.strftime("%H:%M:%S")
    return f"{header}\nupdated {ts}\n\n" + "\n".join(rows) + "\n"


def subscribe(emit):
    """Poll Discord every POLL_INTERVAL seconds; emit a fresh snapshot when
    the state changes (or every 30s as a heartbeat so the tablet sees we're alive)."""
    stop = threading.Event()
    last_text: list[str | None] = [None]
    last_emit_ts: list[float] = [0.0]

    def loop():
        while not stop.is_set():
            try:
                text = _format(_list_discord_windows())
                now = time.time()
                if text != last_text[0] or (now - last_emit_ts[0]) > 30:
                    last_text[0] = text
                    last_emit_ts[0] = now
                    emit(text, replace=True)
            except Exception as e:
                print(f"[discord] poll error: {e}", flush=True)
            stop.wait(POLL_INTERVAL)

    threading.Thread(target=loop, daemon=True, name="dd-discord-poll").start()

    # Emit immediately so the textbox isn't blank on first render.
    try:
        emit(_format(_list_discord_windows()), replace=True)
    except Exception:
        pass

    return stop.set
