"""Extract Windows application icons from .exe files for display in the UI.

Each icon is fetched via win32gui.ExtractIconEx, rendered to a PNG via PIL,
and cached as a base64 data URL (LRU). The Apps overlay and the editor's
Hidden Apps modal both call get_icon_data_url(exe_path).
"""
from __future__ import annotations

import base64
import io
import os
import threading
from typing import Optional

try:
    import win32con
    import win32gui
    import win32ui
    from PIL import Image
    _AVAILABLE = True
except Exception:  # pragma: no cover
    _AVAILABLE = False


_cache: dict[tuple[str, int], Optional[str]] = {}
_cache_lock = threading.Lock()
_CACHE_LIMIT = 1024


def get_icon_data_url(exe_path: str, size: int = 32) -> Optional[str]:
    """Return data:image/png;base64,... for the exe's icon, or None."""
    if not _AVAILABLE or not exe_path:
        return None
    key = (exe_path, size)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    result = _extract(exe_path, size)
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            # Drop ~10% oldest entries (insertion order)
            for k in list(_cache.keys())[: _CACHE_LIMIT // 10]:
                _cache.pop(k, None)
        _cache[key] = result
    return result


def _extract(exe_path: str, size: int) -> Optional[str]:
    if not os.path.exists(exe_path):
        return None
    try:
        large, small = win32gui.ExtractIconEx(exe_path, 0, 1)
    except Exception:
        return None
    hicons = (large or []) + (small or [])
    if not hicons:
        return None
    hicon = hicons[0]
    try:
        png = _hicon_to_png(hicon, size)
        if not png:
            return None
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    finally:
        for h in hicons:
            try:
                win32gui.DestroyIcon(h)
            except Exception:
                pass


def _hicon_to_png(hicon: int, size: int) -> Optional[bytes]:
    """HICON -> PNG bytes. Goes through a memory DC and a PIL frombytes."""
    try:
        hdc_screen = win32gui.GetDC(0)
        try:
            dc = win32ui.CreateDCFromHandle(hdc_screen)
            mem = dc.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(dc, size, size)
            mem.SelectObject(bmp)
            # Solid-fill cleared background; alpha comes from the icon itself.
            mem.FillSolidRect((0, 0, size, size), 0)
            win32gui.DrawIconEx(
                mem.GetSafeHdc(), 0, 0, hicon, size, size, 0, None, win32con.DI_NORMAL
            )
            bits = bmp.GetBitmapBits(True)
        finally:
            win32gui.ReleaseDC(0, hdc_screen)
        img = Image.frombytes("RGBA", (size, size), bits, "raw", "BGRA")
        # Some icon copies come back with the alpha channel zeroed even though
        # the RGB layer is correct (32-bit BMP without per-pixel alpha). Heuristic:
        # if every alpha pixel is 0 but RGB is non-uniform, synthesize a mask
        # by treating exact black corners as transparent.
        a = img.split()[-1]
        if not any(a.getdata()):
            r, g, b, _ = img.split()
            mask = Image.eval(
                Image.merge("RGB", (r, g, b)).convert("L"),
                lambda v: 255 if v > 0 else 0,
            )
            img.putalpha(mask)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None
