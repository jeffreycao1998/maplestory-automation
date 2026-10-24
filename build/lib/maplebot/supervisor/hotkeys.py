"""
Global pause/stop hotkeys (work even when the game window has focus).

Uses a pynput keyboard listener thread. pynput is imported lazily inside `start()` so
this module imports on any platform; if pynput is missing or there is no display,
`start()` raises `HotkeysUnavailable` with a message the app can show as-is.

Callbacks run on the listener thread and must be cheap (e.g. set a flag / call
`BotRunner.pause()`); exceptions they raise are logged and swallowed so the listener
keeps working. Holding a key down only fires once (auto-repeat is ignored).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional, Tuple

log = logging.getLogger(__name__)


class HotkeysUnavailable(RuntimeError):
    """Global hotkeys can't be used on this machine (message is user-facing)."""


# A parsed key spec: ("special", "<pynput Key attribute name>") or ("char", "<c>")
KeySpec = Tuple[str, str]

_SPECIAL_ALIASES = {
    "pause": "pause",
    "break": "pause",
    "scroll_lock": "scroll_lock",
    "scrolllock": "scroll_lock",
    "insert": "insert",
    "ins": "insert",
    "home": "home",
    "end": "end",
    "page_up": "page_up",
    "pageup": "page_up",
    "pgup": "page_up",
    "page_down": "page_down",
    "pagedown": "page_down",
    "pgdn": "page_down",
    "esc": "esc",
    "escape": "esc",
}
for _i in range(1, 25):
    _SPECIAL_ALIASES[f"f{_i}"] = f"f{_i}"


def parse_key(name: str) -> KeySpec:
    """Turn a user key name ("f11", "Pause", "page down", "a") into a KeySpec.

    Raises ValueError with a friendly message for anything unsupported.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("No hotkey set — pick a key such as F11 or F12.")
    raw = name.strip()
    norm = raw.lower().replace(" ", "_").replace("-", "_")
    if norm in _SPECIAL_ALIASES:
        return ("special", _SPECIAL_ALIASES[norm])
    if len(raw) == 1 and raw.isprintable() and not raw.isspace():
        return ("char", raw.lower())
    raise ValueError(
        f"'{name}' isn't a supported hotkey. Use F1–F24, Pause, Scroll Lock, Insert, "
        "Home, End, Page Up, Page Down, Esc, or a single letter/number.")


def _event_spec(key: Any) -> Optional[KeySpec]:
    """Map a pynput key event object to a KeySpec (without importing pynput).

    - pynput.keyboard.Key members have a `.name` (e.g. "f11", "page_up").
    - pynput.keyboard.KeyCode has `.char` (may be None, e.g. for some Fn keys).
    """
    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return ("char", char.lower())
    name = getattr(key, "name", None)
    if isinstance(name, str) and name:
        return ("special", name.lower())
    return None


class Hotkeys:
    def __init__(self, pause_key: str, stop_key: str,
                 on_pause: Callable[[], Any], on_stop: Callable[[], Any]):
        self.pause_spec = parse_key(pause_key)
        self.stop_spec = parse_key(stop_key)
        if self.pause_spec == self.stop_spec:
            raise ValueError("The pause and stop hotkeys must be different keys.")
        self.pause_key = pause_key
        self.stop_key = stop_key
        self._on_pause = on_pause
        self._on_stop = on_stop
        self._held: set = set()
        self._lock = threading.Lock()
        self._listener: Any = None

    # ---- event handling (called from the listener thread; testable directly) --
    def _handle_press(self, key: Any) -> None:
        spec = _event_spec(key)
        if spec is None or spec not in (self.pause_spec, self.stop_spec):
            return
        with self._lock:
            if spec in self._held:     # auto-repeat while held
                return
            self._held.add(spec)
        cb = self._on_stop if spec == self.stop_spec else self._on_pause
        try:
            cb()
        except Exception:
            log.exception("Hotkey callback for %s failed", spec[1])

    def _handle_release(self, key: Any) -> None:
        spec = _event_spec(key)
        if spec is None:
            return
        with self._lock:
            self._held.discard(spec)

    # ---- lifecycle -------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._listener is not None

    def start(self) -> None:
        if self._listener is not None:
            return
        try:
            from pynput import keyboard  # lazy: Windows/desktop only
        except Exception as e:  # ImportError, or Xlib/display errors on import
            raise HotkeysUnavailable(
                "Global hotkeys aren't available on this computer "
                f"(couldn't load the keyboard listener: {e}). "
                "Use the Pause and Stop buttons in the bot window instead.") from e

        def on_press(key):
            self._handle_press(key)

        def on_release(key):
            self._handle_release(key)

        try:
            listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            listener.daemon = True
            listener.start()
        except Exception as e:
            raise HotkeysUnavailable(
                "Global hotkeys couldn't start "
                f"({type(e).__name__}: {e}). "
                "Use the Pause and Stop buttons in the bot window instead.") from e
        self._listener = listener

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        with self._lock:
            self._held.clear()
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            log.exception("Stopping hotkey listener failed")

    def __enter__(self) -> "Hotkeys":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
