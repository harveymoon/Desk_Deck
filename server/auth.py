"""Pairing token: generate once, store in %APPDATA%/Desk_Deck/token, gate all requests."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request, WebSocket, status


def _token_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Desk_Deck"
    base.mkdir(parents=True, exist_ok=True)
    return base / "token"


def load_or_create_token(reset: bool = False) -> str:
    path = _token_path()
    if reset or not path.exists():
        token = secrets.token_urlsafe(32)
        path.write_text(token, encoding="utf-8")
        return token
    return path.read_text(encoding="utf-8").strip()


_TOKEN: str = ""


def set_token(token: str) -> None:
    global _TOKEN
    _TOKEN = token


def get_token() -> str:
    return _TOKEN


def _extract(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("t")


def require_token(request: Request) -> None:
    if _extract(request) != _TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad token")


def is_localhost(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


async def ws_check_token(ws: WebSocket, token: str | None) -> bool:
    if token != _TOKEN:
        await ws.close(code=4401)
        return False
    return True
