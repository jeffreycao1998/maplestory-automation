"""
Turn a key the user pressed in the GUI (a Tk event) into the bot's key name.

No tkinter import here: the GUI passes ``event.keysym`` / ``event.keycode`` in as plain
values, so this module is unit-testable on a machine without a display.

Bot key names are the lowercase names used by ``maplebot.controls`` (``"ctrl"``,
``"alt"``, ``"left"``, ``"a"``, ``"f1"``, ``"pageup"``, ``"comma"`` ...). Left and right
modifiers both map to the plain name (``Control_R`` -> ``"ctrl"``) because that is what
people mean when they say "my attack is Ctrl"; ``rctrl`` etc. stay in the dropdown.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Tk keysym -> bot key name. Letters, digits and F-keys are handled by rules below.
KEYSYM_TO_NAME = {
    # modifiers (left and right versions map to the same key)
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt", "Meta_L": "alt", "Meta_R": "alt",
    "Shift_L": "shift", "Shift_R": "shift",
    "ISO_Level3_Shift": "alt",           # AltGr on some layouts
    # arrows
    "Left": "left", "Right": "right", "Up": "up", "Down": "down",
    # navigation block
    "Prior": "pageup", "Next": "pagedown", "Page_Up": "pageup", "Page_Down": "pagedown",
    "Home": "home", "End": "end", "Insert": "insert", "Delete": "delete",
    # common keys
    "space": "space", "Return": "enter", "KP_Enter": "enter", "Escape": "esc",
    "Tab": "tab", "BackSpace": "backspace", "Caps_Lock": "capslock",
    # punctuation (US layout keysyms -> maplebot.controls names)
    "minus": "minus", "equal": "equals", "bracketleft": "lbracket",
    "bracketright": "rbracket", "semicolon": "semicolon", "apostrophe": "quote",
    "quoteright": "quote", "grave": "backtick", "quoteleft": "backtick",
    "comma": "comma", "period": "period", "slash": "slash", "backslash": "backslash",
}

# Shifted symbols that Tk reports when Shift is held -> the unshifted key.
SHIFTED_TO_NAME = {
    "exclam": "1", "at": "2", "numbersign": "3", "dollar": "4", "percent": "5",
    "asciicircum": "6", "ampersand": "7", "asterisk": "8", "parenleft": "9",
    "parenright": "0", "underscore": "minus", "plus": "equals", "braceleft": "lbracket",
    "braceright": "rbracket", "colon": "semicolon", "quotedbl": "quote",
    "asciitilde": "backtick", "less": "comma", "greater": "period", "question": "slash",
    "bar": "backslash",
}

# Windows virtual-key codes (Tk's event.keycode on Windows) -> bot key name.
# Used when the keysym is missing or unhelpful (e.g. "??").
VK_TO_NAME = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x10: "shift", 0x11: "ctrl",
    0x12: "alt", 0x14: "capslock", 0x1B: "esc", 0x20: "space", 0x21: "pageup",
    0x22: "pagedown", 0x23: "end", 0x24: "home", 0x25: "left", 0x26: "up",
    0x27: "right", 0x28: "down", 0x2D: "insert", 0x2E: "delete",
    0xA0: "shift", 0xA1: "shift", 0xA2: "ctrl", 0xA3: "ctrl", 0xA4: "alt", 0xA5: "alt",
    0xBA: "semicolon", 0xBB: "equals", 0xBC: "comma", 0xBD: "minus", 0xBE: "period",
    0xBF: "slash", 0xC0: "backtick", 0xDB: "lbracket", 0xDC: "backslash",
    0xDD: "rbracket", 0xDE: "quote",
    **{0x30 + d: str(d) for d in range(10)},
    **{0x41 + i: chr(ord("a") + i) for i in range(26)},
    **{0x70 + i: f"f{i + 1}" for i in range(24)},
}

# Friendly names to show the user ("Ctrl" rather than "ctrl").
DISPLAY = {
    "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "space": "Space", "enter": "Enter",
    "esc": "Esc", "pageup": "Page Up", "pagedown": "Page Down", "delete": "Delete",
    "insert": "Insert", "home": "Home", "end": "End", "left": "Left arrow",
    "right": "Right arrow", "up": "Up arrow", "down": "Down arrow", "tab": "Tab",
    "backspace": "Backspace", "capslock": "Caps Lock", "rctrl": "Right Ctrl",
    "ralt": "Right Alt", "rshift": "Right Shift", "minus": "-", "equals": "=",
    "lbracket": "[", "rbracket": "]", "semicolon": ";", "quote": "'", "comma": ",",
    "period": ".", "slash": "/", "backslash": "\\", "backtick": "`",
}


def keysym_to_name(keysym: Optional[str]) -> Optional[str]:
    """Map a Tk keysym to a bot key name, or None if we don't know it."""
    if not keysym:
        return None
    if keysym in KEYSYM_TO_NAME:
        return KEYSYM_TO_NAME[keysym]
    if keysym in SHIFTED_TO_NAME:
        return SHIFTED_TO_NAME[keysym]
    if len(keysym) == 1 and keysym.isascii() and keysym.isalnum():
        return keysym.lower()                         # "a", "A" (shift held), "7"
    low = keysym.lower()
    if low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        return low                                    # "F1" .. "F24"
    return None


def vk_to_name(keycode: Optional[int]) -> Optional[str]:
    """Map a Windows virtual-key code (Tk event.keycode on Windows) to a bot key name."""
    if keycode is None:
        return None
    try:
        return VK_TO_NAME.get(int(keycode))
    except (TypeError, ValueError):
        return None


def name_from_event(keysym: Optional[str], keycode: Optional[int] = None, *,
                    windows: bool = False,
                    available: Optional[Iterable[str]] = None) -> Optional[str]:
    """Best bot key name for a key press, or None if unsupported.

    ``windows``: also try ``keycode`` as a Windows virtual-key code (Tk on Windows
    reports VK codes there). ``available``: if given, only names in it are returned
    (the list from ``maplebot.controls.available_keys()``).
    """
    allowed = set(available) if available is not None else None
    candidates = [keysym_to_name(keysym)]
    if windows:
        candidates.append(vk_to_name(keycode))
    for name in candidates:
        if name and (allowed is None or name in allowed):
            return name
    return None


def display_name(name: Optional[str]) -> str:
    """Human-friendly label for a bot key name ("" -> "(none)")."""
    if not name:
        return "(none)"
    if name in DISPLAY:
        return DISPLAY[name]
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    return name.upper() if len(name) == 1 else name
