"""Textbox source: streams `log` lines TD pushes via {t:"log", line:"..."}.

Append semantics (vs. the replace-snapshot pattern the other td_*
providers use): each emitted line accumulates in the textbox so the user
can scroll back through recent activity.
"""
from __future__ import annotations

import time

from .. import td


def subscribe(emit):
    # Print a small header so the textbox shows it's wired up even if TD
    # hasn't logged anything yet.
    emit(f"[{time.strftime('%H:%M:%S')}] td textport stream attached\n")

    def on_log(line):
        if not line:
            return
        # Ensure trailing newline so successive lines stack nicely
        if not line.endswith("\n"):
            line = line + "\n"
        emit(line)

    return td.subscribe("log", on_log)
