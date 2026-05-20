"""Example textbox source: tail a fixed log file (configure via env DD_TAIL_FILE)."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

LOG_PATH = os.environ.get("DD_TAIL_FILE") or str(Path.home() / "desk_deck_demo.log")


def subscribe(emit):
    """Stream new lines from LOG_PATH into the textbox.

    Creates the file if missing, seeks to the end, and emits any new lines.
    Returns a cleanup callable.
    """
    Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    f = open(LOG_PATH, "a+", encoding="utf-8", errors="replace")
    f.seek(0, 2)  # end of file
    stop = threading.Event()
    emit(f"[tail] watching {LOG_PATH}\n")

    def loop() -> None:
        while not stop.is_set():
            line = f.readline()
            if line:
                emit(line)
            else:
                time.sleep(0.2)

    t = threading.Thread(target=loop, daemon=True, name="dd-tail-log")
    t.start()

    def cleanup() -> None:
        stop.set()
        try:
            f.close()
        except Exception:
            pass

    return cleanup
