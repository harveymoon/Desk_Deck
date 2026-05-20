"""Entrypoint: `python -m server` — generate token, print QR + URL, run uvicorn."""
from __future__ import annotations

import argparse
import socket
import sys

import qrcode
import uvicorn

from . import auth, log_buffer

# Force UTF-8 stdout so the QR's block glyphs render on Windows cp1252 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Mirror stdout/stderr into the in-memory log buffer so the editor's Logs tab
# can tail them over SSE. Must run before uvicorn's StreamHandlers attach.
log_buffer.install()


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    try:
        qr.print_ascii(invert=True)
    except UnicodeEncodeError:
        # Fallback for terminals that can't render block chars.
        qr.print_tty()


def main() -> None:
    ap = argparse.ArgumentParser(prog="desk_deck")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--reset-token", action="store_true",
                    help="Rotate the pairing token (invalidates all paired devices)")
    ap.add_argument("--reload", action="store_true", help="uvicorn auto-reload (dev)")
    args = ap.parse_args()

    token = auth.load_or_create_token(reset=args.reset_token)
    auth.set_token(token)

    ip = _lan_ip()
    url = f"http://{ip}:{args.port}/?t={token}"
    pair_url = f"http://{ip}:{args.port}/pair"
    editor_url = f"http://127.0.0.1:{args.port}/editor"

    print()
    print(f"  Tablet:  {url}")
    print(f"  Pair:    {pair_url}")
    print(f"  Editor:  {editor_url}")
    print()
    _print_qr(url)
    print()

    uvicorn.run(
        "server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
