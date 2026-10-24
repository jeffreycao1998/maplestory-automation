"""
Screen capture of the game's client area.

Windows-only calls (ctypes.windll, user32, shcore) live inside functions so this module
imports on Linux; there `find_client_area` raises WindowNotFound and
`enable_dpi_awareness` does nothing.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import List, Optional, Tuple

import numpy as np

from maplebot.types import Box, Frame

log = logging.getLogger(__name__)

WINDOW_RECHECK_S = 1.0   # re-find the window at most this often during grab()


class WindowNotFound(RuntimeError):
    """No visible, non-minimised window whose title contains the requested text."""


def _not_found(title: str) -> WindowNotFound:
    return WindowNotFound(
        f"Couldn't find a window with '{title}' in its title. "
        "Is the game open and not minimised?"
    )


def enable_dpi_awareness() -> None:
    """Make this process per-monitor DPI aware so coordinates are physical pixels.

    Call once at startup, before any window/capture call. No-op off Windows; never raises.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            return
        except Exception:
            pass
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        log.debug("could not enable DPI awareness", exc_info=True)


def _enum_windows(title_substring: str) -> List[Tuple[int, str, Box]]:
    """[(hwnd, title, client_box)] for visible, non-minimised windows matching the title."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    needle = title_substring.lower()
    found: List[Tuple[int, str, Box]] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if needle not in title.lower():
                return True
            rect = wintypes.RECT()
            if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
                return True
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return True
            pt = wintypes.POINT(0, 0)
            if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
                return True
            found.append((int(hwnd or 0), title, (int(pt.x), int(pt.y), int(w), int(h))))
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found


def find_client_area(window_title: str) -> Box:
    """(left, top, width, height) of the matching window's client area, physical pixels.

    Case-insensitive substring match on visible, non-minimised windows; the largest
    client area wins. Raises WindowNotFound (also on non-Windows platforms).
    """
    title = window_title or "MapleStory"
    if sys.platform != "win32":
        raise _not_found(title)
    try:
        matches = _enum_windows(title)
    except WindowNotFound:
        raise
    except Exception as e:  # pragma: no cover - Windows only
        raise _not_found(title) from e
    if not matches:
        raise _not_found(title)
    _, _, box = max(matches, key=lambda m: m[2][2] * m[2][3])
    return box


class ScreenCapture:
    """Grabs the game's client area with mss. Finds the window on construction."""

    def __init__(self, window_title: str):
        self.window_title = window_title
        self._box: Box = find_client_area(window_title)
        self._last_check = time.monotonic()
        self._sct = None

    @property
    def box(self) -> Box:
        return self._box

    def _sct_get(self):
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    def _refresh_box(self, now: float) -> None:
        if now - self._last_check < WINDOW_RECHECK_S:
            return
        self._last_check = now
        try:
            box = find_client_area(self.window_title)
        except WindowNotFound:
            # Minimised or closed for a moment: keep grabbing the last known spot; the
            # Perceiver will flag the junk frame (minimap missing) and the Supervisor pauses.
            return
        if box != self._box:
            log.info("game window moved/resized: %s -> %s", self._box, box)
            self._box = box

    def grab(self) -> Frame:
        now = time.monotonic()
        self._refresh_box(now)
        left, top, w, h = self._box
        shot = self._sct_get().grab({"left": left, "top": top, "width": w, "height": h})
        bgra = np.asarray(shot, dtype=np.uint8)           # (h, w, 4) BGRA
        image = np.ascontiguousarray(bgra[:, :, :3])
        return Frame(image=image, t=time.monotonic(), window=self._box)

    def close(self) -> None:
        sct, self._sct = self._sct, None
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass

    def __enter__(self) -> "ScreenCapture":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
