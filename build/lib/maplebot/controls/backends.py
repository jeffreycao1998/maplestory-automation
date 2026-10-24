"""
Key-input backends: the only place that talks to the OS keyboard.

- ``InputBackend``     -- the two-method interface the Controller uses.
- ``FakeBackend``      -- records every event; used by all tests (Linux sandbox).
- ``SendInputBackend`` -- real input on Windows via ``user32.SendInput`` with hardware
                          scan codes (KEYEVENTF_SCANCODE). DirectInput games such as
                          MapleStory read scan codes, not virtual-key codes.
- ``available_keys`` / ``normalize_key`` -- the key-name vocabulary, shared with the GUI.

This module must import on Linux: every Windows-specific call lives inside a function
or the ``SendInputBackend`` constructor. The ctypes structure definitions use only
fixed-width ctypes types, so their layout can be checked on Linux too.
"""
from __future__ import annotations

import functools
import sys
import threading
import time
from typing import Callable, Iterable, List, Optional, Protocol, Set, Tuple, runtime_checkable

# ---------------------------------------------------------------------------
# Key names and scan codes (IBM PC "set 1", what SendInput's wScan expects)
# ---------------------------------------------------------------------------

#: canonical name -> (scan code, is_extended). Extended keys are sent with the 0xE0
#: prefix, which SendInput expresses as KEYEVENTF_EXTENDEDKEY. Without it, the arrow
#: keys would arrive as numpad 4/6/8/2 -- the classic pydirectinput bug.
_SCAN_CODES: dict = {}


def _add(names: Iterable[str], codes: Iterable[int], extended: bool = False) -> None:
    for n, c in zip(names, codes):
        _SCAN_CODES[n] = (c, extended)


_add("1234567890", range(0x02, 0x0C))
_add("qwertyuiop", range(0x10, 0x1A))
_add("asdfghjkl", range(0x1E, 0x27))
_add("zxcvbnm", range(0x2C, 0x33))
_add([f"f{i}" for i in range(1, 11)], range(0x3B, 0x45))
_add(["f11", "f12"], [0x57, 0x58])
_add(["esc", "minus", "equals", "backspace", "tab", "lbracket", "rbracket", "enter",
      "ctrl", "semicolon", "quote", "backtick", "shift", "backslash",
      "comma", "period", "slash", "rshift", "alt", "space"],
     [0x01, 0x0C, 0x0D, 0x0E, 0x0F, 0x1A, 0x1B, 0x1C,
      0x1D, 0x27, 0x28, 0x29, 0x2A, 0x2B,
      0x33, 0x34, 0x35, 0x36, 0x38, 0x39])
_add(["insert", "delete", "home", "end", "pageup", "pagedown",
      "left", "right", "up", "down", "rctrl", "ralt"],
     [0x52, 0x53, 0x47, 0x4F, 0x49, 0x51,
      0x4B, 0x4D, 0x48, 0x50, 0x1D, 0x38], extended=True)

#: Names that need KEYEVENTF_EXTENDEDKEY.
EXTENDED_KEYS = frozenset(n for n, (_, ext) in _SCAN_CODES.items() if ext)

#: Order shown in the GUI dropdowns: most likely keybinds first.
_GUI_ORDER: List[str] = (
    ["ctrl", "alt", "shift", "space", "rctrl", "ralt", "rshift"]
    + ["left", "right", "up", "down"]
    + ["insert", "delete", "home", "end", "pageup", "pagedown"]
    + list("abcdefghijklmnopqrstuvwxyz")
    + list("1234567890")
    + [f"f{i}" for i in range(1, 13)]
    + ["minus", "equals", "lbracket", "rbracket", "semicolon", "quote",
       "comma", "period", "slash", "backslash", "backtick"]
    + ["enter", "tab", "esc", "backspace"]
)
assert sorted(_GUI_ORDER) == sorted(_SCAN_CODES), "GUI order and scan table disagree"

_ALIASES = {
    "control": "ctrl", "lctrl": "ctrl", "lcontrol": "ctrl", "leftctrl": "ctrl",
    "rcontrol": "rctrl", "rightctrl": "rctrl",
    "lalt": "alt", "leftalt": "alt", "menu": "alt", "rightalt": "ralt", "altgr": "ralt",
    "lshift": "shift", "leftshift": "shift", "rightshift": "rshift",
    "del": "delete", "ins": "insert",
    "pgup": "pageup", "page_up": "pageup", "page up": "pageup",
    "pgdn": "pagedown", "pgdown": "pagedown", "page_down": "pagedown", "page down": "pagedown",
    "escape": "esc", "return": "enter", "spacebar": "space", " ": "space",
    "arrowleft": "left", "arrowright": "right", "arrowup": "up", "arrowdown": "down",
    "-": "minus", "=": "equals", "[": "lbracket", "]": "rbracket", ";": "semicolon",
    "'": "quote", ",": "comma", ".": "period", "/": "slash", "\\": "backslash",
    "`": "backtick", "grave": "backtick", "tilde": "backtick", "apostrophe": "quote",
    "dash": "minus", "hyphen": "minus", "equal": "equals", "plus": "equals",
    "dot": "period", "bksp": "backspace",
}


def available_keys() -> List[str]:
    """Every key name the bot can press, in a sensible dropdown order."""
    return list(_GUI_ORDER)


def normalize_key(name: str) -> str:
    """Return the canonical key name for ``name`` (case-insensitive, aliases accepted).

    Raises ValueError with a friendly message for empty or unknown names.
    """
    if not isinstance(name, str):
        raise ValueError(f"Key name must be text, got {name!r}")
    raw = name if name == " " else name.strip()
    if raw == "":
        raise ValueError("No key set. Pick a key from the list.")
    low = raw.lower()
    if low in _SCAN_CODES:
        return low
    if low in _ALIASES:
        return _ALIASES[low]
    squashed = low.replace(" ", "").replace("_", "").replace("-", "")
    if squashed in _SCAN_CODES:
        return squashed
    if squashed in _ALIASES:
        return _ALIASES[squashed]
    raise ValueError(
        f"Unknown key {name!r}. Use a name like 'ctrl', 'alt', 'shift', 'space', 'z', "
        f"'5', 'f1', 'left', 'pageup' or 'comma' (see the keybind list).")


def scan_code(name: str) -> Tuple[int, bool]:
    """(scan code, is_extended) for a key name. Raises ValueError for unknown names."""
    return _SCAN_CODES[normalize_key(name)]


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

@runtime_checkable
class InputBackend(Protocol):
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...


# ---------------------------------------------------------------------------
# Fake (tests)
# ---------------------------------------------------------------------------

class BackendError(RuntimeError):
    """Raised by FakeBackend to simulate a failing OS input call."""


class FakeBackend:
    """Records key events instead of pressing anything.

    ``events``  -- list of (t, key, down) in call order, t from ``clock``.
    ``held``    -- keys currently down.
    ``fail_on`` -- keys whose key_down raises BackendError (the key is NOT pressed).
    ``fail_up_on`` -- keys whose key_up raises BackendError (the key STAYS held).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 fail_on: Optional[Iterable[str]] = None,
                 fail_up_on: Optional[Iterable[str]] = None) -> None:
        self.clock = clock
        self.events: List[Tuple[float, str, bool]] = []
        self.held: Set[str] = set()
        self.fail_on: Set[str] = set(fail_on or ())
        self.fail_up_on: Set[str] = set(fail_up_on or ())
        self._lock = threading.Lock()

    def key_down(self, key: str) -> None:
        with self._lock:
            if key in self.fail_on:
                raise BackendError(f"simulated key_down failure for {key!r}")
            self.events.append((self.clock(), key, True))
            self.held.add(key)

    def key_up(self, key: str) -> None:
        with self._lock:
            if key in self.fail_up_on:
                raise BackendError(f"simulated key_up failure for {key!r}")
            self.events.append((self.clock(), key, False))
            self.held.discard(key)

    # helpers for tests
    def sequence(self) -> List[Tuple[str, bool]]:
        """Events without timestamps: [(key, down), ...]."""
        return [(k, d) for _, k, d in self.events]

    def clear(self) -> None:
        self.events.clear()


# ---------------------------------------------------------------------------
# Windows SendInput
# ---------------------------------------------------------------------------

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


@functools.lru_cache(maxsize=None)
def win32_structs():
    """Build the INPUT structure family with ctypes.

    Uses fixed-width types (not ctypes.wintypes, whose DWORD is c_ulong -- 8 bytes on
    64-bit Linux) so the Windows layout can be verified on any OS:
    64-bit Windows: KEYBDINPUT 24, MOUSEINPUT 32, HARDWAREINPUT 8, INPUT 40 bytes.
    The union must include MOUSEINPUT (the largest member) or SendInput rejects cbSize.
    """
    import ctypes

    WORD = ctypes.c_uint16
    DWORD = ctypes.c_uint32
    LONG = ctypes.c_int32
    ULONG_PTR = ctypes.c_size_t        # pointer-sized unsigned

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                    ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                    ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", DWORD), ("u", _INPUTUNION)]

    return KEYBDINPUT, MOUSEINPUT, HARDWAREINPUT, INPUT


def keyboard_flags(name: str, up: bool) -> Tuple[int, int]:
    """(wScan, dwFlags) that SendInputBackend sends for this key event."""
    code, extended = scan_code(name)
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= KEYEVENTF_KEYUP
    return code, flags


class SendInputBackend:
    """Real keyboard input on Windows via user32.SendInput with scan codes.

    If the game runs as administrator the bot must too: Windows silently drops input
    from a non-elevated process into an elevated window (SendInput then returns 0).
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Real key input only works on Windows")
        import ctypes

        _, _, _, INPUT = win32_structs()
        self._ctypes = ctypes
        self._INPUT = INPUT
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send = user32.SendInput
        send.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
        send.restype = ctypes.c_uint
        self._send = send

    def _send_key(self, key: str, up: bool) -> None:
        ctypes = self._ctypes
        scan, flags = keyboard_flags(key, up)
        inp = self._INPUT(type=INPUT_KEYBOARD)
        inp.ki.wVk = 0
        inp.ki.wScan = scan
        inp.ki.dwFlags = flags
        inp.ki.time = 0
        inp.ki.dwExtraInfo = 0
        sent = self._send(1, ctypes.byref(inp), ctypes.sizeof(self._INPUT))
        if sent != 1:
            err = ctypes.get_last_error()
            raise OSError(
                err,
                f"Windows refused the key {'release' if up else 'press'} for {key!r} "
                f"(SendInput error {err}). If MapleStory runs as administrator, run the "
                f"bot as administrator too.")

    def key_down(self, key: str) -> None:
        self._send_key(key, up=False)

    def key_up(self, key: str) -> None:
        self._send_key(key, up=True)


def default_backend() -> InputBackend:
    """The real input backend for this OS. Only Windows is supported."""
    if sys.platform == "win32":
        return SendInputBackend()
    raise RuntimeError("Real key input only works on Windows")
